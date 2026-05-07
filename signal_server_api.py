"""
Complete API Server with Database for Z-BOT Signal Server
Deploy on Render.com with PostgreSQL

Features:
- User authentication via access tokens
- Provider CRUD operations
- Subscription management
- Request/approval workflow
- Signal history
- WebSocket integration for real-time signals
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Set, List
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import uvicorn

from signal_server_models import (
    Base, User, Provider, Subscription, SubscriptionRequest, Signal,
    get_db_engine, get_db_session, init_db
)


# ============================================================
# PYDANTIC REQUEST MODELS
# ============================================================

class RegisterRequest(BaseModel):
    email: str
    name: str


class ProviderCreateRequest(BaseModel):
    name: str
    membership_value: str = "Free"
    observations: str = ""


class ProviderUpdateRequest(BaseModel):
    name: Optional[str] = None
    membership_value: Optional[str] = None
    observations: Optional[str] = None


class SubscriptionRequestCreate(BaseModel):
    email: str
    contact: str


class SubscriptionApproveRequest(BaseModel):
    real_access: bool = False
    demo_access: bool = True


class SetExpirationRequest(BaseModel):
    expires_at: datetime


class TokenAuthRequest(BaseModel):
    token: str

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# WEBSOCKET CONNECTION MANAGER
# ============================================================

class WebSocketManager:
    """Manage WebSocket connections with per-provider routing"""
    def __init__(self):
        self.provider_connections: Dict[str, Set[WebSocket]] = {}
        self.subscriber_connections: Dict[WebSocket, Set[str]] = {}  # ws -> {provider_ids}
    
    async def connect_provider(self, websocket: WebSocket, provider_id: str):
        await websocket.accept()
        if provider_id not in self.provider_connections:
            self.provider_connections[provider_id] = set()
        self.provider_connections[provider_id].add(websocket)
        logger.info(f"Provider {provider_id} connected")
    
    async def connect_subscriber(self, websocket: WebSocket):
        await websocket.accept()
        self.subscriber_connections[websocket] = set()
        logger.info("Subscriber connected")
    
    def disconnect_provider(self, websocket: WebSocket, provider_id: str):
        if provider_id in self.provider_connections:
            self.provider_connections[provider_id].discard(websocket)
            if not self.provider_connections[provider_id]:
                del self.provider_connections[provider_id]
        logger.info(f"Provider {provider_id} disconnected")
    
    def disconnect_subscriber(self, websocket: WebSocket):
        if websocket in self.subscriber_connections:
            del self.subscriber_connections[websocket]
        logger.info("Subscriber disconnected")
    
    def join_provider(self, websocket: WebSocket, provider_id: str):
        """Subscriber joins a provider to receive its signals"""
        if websocket in self.subscriber_connections:
            self.subscriber_connections[websocket].add(provider_id)
        logger.info(f"Subscriber joined provider {provider_id}")
    
    def leave_provider(self, websocket: WebSocket, provider_id: str):
        """Subscriber leaves a provider"""
        if websocket in self.subscriber_connections:
            self.subscriber_connections[websocket].discard(provider_id)
        logger.info(f"Subscriber left provider {provider_id}")
    
    async def broadcast_to_provider_subscribers(self, provider_id: str, message: str):
        """Send signal only to subscribers of a specific provider"""
        disconnected = []
        for subscriber_ws, provider_ids in self.subscriber_connections.items():
            if provider_id in provider_ids:
                try:
                    await subscriber_ws.send_text(message)
                except:
                    disconnected.append(subscriber_ws)
        # Clean up disconnected subscribers
        for ws in disconnected:
            self.disconnect_subscriber(ws)
        if disconnected:
            logger.info(f"Cleaned up {len(disconnected)} disconnected subscribers")

# Global WebSocket manager
ws_manager = WebSocketManager()

# ============================================================
# DATABASE SETUP
# ============================================================

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    logger.warning("⚠️ DATABASE_URL not set. Using SQLite for development.")
    DATABASE_URL = "sqlite:///./zbot_signals.db"

engine = get_db_engine(DATABASE_URL)
init_db(engine)

def get_db():
    """Database session dependency for FastAPI (sync generator)"""
    db = get_db_session(engine)
    try:
        yield db
    finally:
        db.close()


# ============================================================
# APP SETUP
# ============================================================

app = FastAPI(title="Z-BOT Signal Server", version="2.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# AUTHENTICATION HELPER
# ============================================================

def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    """Get current user from authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    # Extract token (format: "Bearer <token>" or just "<token>")
    token = authorization.replace("Bearer ", "").strip()
    
    user = db.query(User).filter(User.access_token == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid access token")
    
    return user


# ============================================================
# USER ENDPOINTS
# ============================================================

@app.post("/api/v1/auth/register")
async def register_user(
    email: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    body: Optional[RegisterRequest] = Body(None),
    db: Session = Depends(get_db)
):
    """Register a new user and return access token (supports both query params and JSON body)"""
    import uuid
    
    # Accept data from either query params or JSON body
    user_email = email or (body.email if body else None)
    user_name = name or (body.name if body else None)
    
    if not user_email or not user_name:
        raise HTTPException(status_code=400, detail="Email and name are required")
    
    logger.info(f"Registration request: email={user_email}, name={user_name}")
    
    # Check if user exists
    existing_user = db.query(User).filter(User.email == user_email).first()
    if existing_user:
        logger.info(f"User already exists: {user_email}")
        return {
            "status": "success",
            "user_id": existing_user.id,
            "access_token": existing_user.access_token,
            "email": existing_user.email
        }
    
    # Create new user
    new_user = User(
        id=str(uuid.uuid4()),
        email=user_email,
        name=user_name,
        access_token=str(uuid.uuid4())
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    logger.info(f"✅ New user registered: {user_email}")
    
    return {
        "status": "success",
        "user_id": new_user.id,
        "access_token": new_user.access_token,
        "email": new_user.email
    }


@app.get("/api/v1/auth/me")
async def get_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return {
        "status": "success",
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "created_at": current_user.created_at.isoformat()
        }
    }


# ============================================================
# PROVIDER ENDPOINTS
# ============================================================

@app.post("/api/v1/providers")
async def create_provider(
    body: ProviderCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new signal provider"""
    import uuid
    
    provider = Provider(
        id=str(uuid.uuid4()),
        owner_id=current_user.id,
        name=body.name,
        membership_value=body.membership_value,
        observations=body.observations
    )
    
    db.add(provider)
    db.commit()
    db.refresh(provider)
    
    logger.info(f"✅ Provider created: {body.name} by {current_user.email}")
    
    return {
        "status": "success",
        "provider": provider.to_dict()
    }


@app.get("/api/v1/providers")
async def list_providers(
    subscribed: bool = False,
    is_owner: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List providers based on filters"""
    query = db.query(Provider)
    
    if is_owner:
        # Show user's own providers
        query = query.filter(Provider.owner_id == current_user.id)
    elif subscribed:
        # Show providers user is subscribed to
        subscriptions = db.query(Subscription).filter(
            Subscription.user_id == current_user.id,
            Subscription.is_active == True
        ).all()
        
        provider_ids = [sub.provider_id for sub in subscriptions]
        query = query.filter(Provider.id.in_(provider_ids))
    
    # Only show active providers
    query = query.filter(Provider.is_active == True)
    
    providers = query.all()
    
    # Enrich with additional data
    result = []
    for provider in providers:
        data = provider.to_dict()
        
        # Add subscription status
        if subscribed:
            subscription = db.query(Subscription).filter(
                Subscription.user_id == current_user.id,
                Subscription.provider_id == provider.id,
                Subscription.is_active == True
            ).first()
            if subscription:
                data['subscription_id'] = subscription.id
                data['real_access'] = subscription.real_access
                data['demo_access'] = subscription.demo_access
                data['expires_at'] = subscription.expires_at.isoformat() if subscription.expires_at else None
        
        # Add request status
        if not subscribed and not is_owner:
            request = db.query(SubscriptionRequest).filter(
                SubscriptionRequest.user_id == current_user.id,
                SubscriptionRequest.provider_id == provider.id,
                SubscriptionRequest.status == 'pending'
            ).first()
            data['subscription_requested'] = request is not None
        
        result.append(data)
    
    return {
        "status": "success",
        "providers": result,
        "count": len(result)
    }


@app.put("/api/v1/providers/{provider_id}")
async def update_provider(
    provider_id: str,
    body: ProviderUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a provider (owner only)"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    if body.name:
        provider.name = body.name
    if body.membership_value:
        provider.membership_value = body.membership_value
    if body.observations:
        provider.observations = body.observations
    
    db.commit()
    db.refresh(provider)
    
    logger.info(f"✅ Provider updated: {provider.name}")
    
    return {
        "status": "success",
        "provider": provider.to_dict()
    }


@app.delete("/api/v1/providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a provider (owner only)"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    provider.is_active = False
    db.commit()
    
    logger.info(f"✅ Provider deleted: {provider.name}")
    
    return {
        "status": "success",
        "message": "Provider deleted successfully"
    }


# ============================================================
# SUBSCRIPTION REQUEST ENDPOINTS
# ============================================================

@app.post("/api/v1/providers/{provider_id}/requests")
async def request_subscription(
    provider_id: str,
    body: SubscriptionRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Request subscription to a provider"""
    import uuid
    
    # Check if provider exists
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    
    # Check if already requested
    existing_request = db.query(SubscriptionRequest).filter(
        SubscriptionRequest.user_id == current_user.id,
        SubscriptionRequest.provider_id == provider_id,
        SubscriptionRequest.status == 'pending'
    ).first()
    
    if existing_request:
        return {
            "status": "success",
            "message": "Subscription request already pending"
        }
    
    # Create request
    request = SubscriptionRequest(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        provider_id=provider_id,
        contact=body.contact,
        status='pending'
    )
    
    db.add(request)
    db.commit()
    db.refresh(request)
    
    logger.info(f"📥 Subscription request: {current_user.email} -> {provider.name}")
    
    return {
        "status": "success",
        "request": request.to_dict()
    }


@app.get("/api/v1/providers/{provider_id}/requests")
async def get_subscription_requests(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get subscription requests for a provider (owner only)"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found or not authorized")
    
    requests = db.query(SubscriptionRequest).filter(
        SubscriptionRequest.provider_id == provider_id,
        SubscriptionRequest.status == 'pending'
    ).all()
    
    return {
        "status": "success",
        "requests": [req.to_dict() for req in requests],
        "count": len(requests)
    }


@app.post("/api/v1/providers/{provider_id}/requests/{request_id}/approve")
async def approve_subscription_request(
    provider_id: str,
    request_id: str,
    body: SubscriptionApproveRequest = SubscriptionApproveRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Approve a subscription request"""
    import uuid
    from datetime import timedelta
    
    # Verify provider ownership
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found or not authorized")
    
    # Get request
    request = db.query(SubscriptionRequest).filter(
        SubscriptionRequest.id == request_id,
        SubscriptionRequest.provider_id == provider_id
    ).first()
    
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    # Update request status
    request.status = 'approved'
    
    # Create subscription
    subscription = Subscription(
        id=str(uuid.uuid4()),
        user_id=request.user_id,
        provider_id=provider_id,
        contact=request.contact,
        real_access=body.real_access,
        demo_access=body.demo_access,
        expires_at=datetime.utcnow() + timedelta(days=30)  # Default 30 days
    )
    
    db.add(subscription)
    db.commit()
    
    logger.info(f"✅ Subscription approved: {request.user_email} -> {provider.name}")
    
    return {
        "status": "success",
        "message": "Subscription approved",
        "subscription": subscription.to_dict()
    }


@app.post("/api/v1/providers/{provider_id}/requests/{request_id}/reject")
async def reject_subscription_request(
    provider_id: str,
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Reject a subscription request"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found or not authorized")
    
    request = db.query(SubscriptionRequest).filter(
        SubscriptionRequest.id == request_id,
        SubscriptionRequest.provider_id == provider_id
    ).first()
    
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    request.status = 'rejected'
    db.commit()
    
    logger.info(f"❌ Subscription rejected: {request.user_email} -> {provider.name}")
    
    return {
        "status": "success",
        "message": "Subscription request rejected"
    }


# ============================================================
# SUBSCRIPTION ENDPOINTS
# ============================================================

@app.get("/api/v1/providers/{provider_id}/subscriptions")
async def get_subscriptions(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all active subscriptions for a provider (owner only)"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found or not authorized")
    
    subscriptions = db.query(Subscription).filter(
        Subscription.provider_id == provider_id,
        Subscription.is_active == True
    ).all()
    
    return {
        "status": "success",
        "subscriptions": [sub.to_dict() for sub in subscriptions],
        "count": len(subscriptions)
    }


@app.post("/api/v1/providers/{provider_id}/subscriptions/{subscription_id}/unsubscribe")
async def unsubscribe(
    provider_id: str,
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Unsubscribe from a provider"""
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id,
        Subscription.user_id == current_user.id,
        Subscription.provider_id == provider_id
    ).first()
    
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    subscription.is_active = False
    db.commit()
    
    logger.info(f"📤 Unsubscribed: {current_user.email} from provider {provider_id}")
    
    return {
        "status": "success",
        "message": "Successfully unsubscribed"
    }


@app.post("/api/v1/providers/{provider_id}/subscriptions/{subscription_id}/set-expiration")
async def set_subscription_expiration(
    provider_id: str,
    subscription_id: str,
    body: SetExpirationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Set subscription expiration date (owner only)"""
    provider = db.query(Provider).filter(
        Provider.id == provider_id,
        Provider.owner_id == current_user.id
    ).first()
    
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found or not authorized")
    
    subscription = db.query(Subscription).filter(
        Subscription.id == subscription_id,
        Subscription.provider_id == provider_id
    ).first()
    
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    subscription.expires_at = body.expires_at
    db.commit()
    
    logger.info(f"⏰ Expiration set for subscription {subscription_id}: {body.expires_at}")
    
    return {
        "status": "success",
        "message": "Expiration date updated",
        "expires_at": body.expires_at.isoformat()
    }


# ============================================================
# SIGNAL HISTORY ENDPOINTS
# ============================================================

@app.get("/api/v1/signals/{provider_id}")
async def get_signal_history(
    provider_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get recent signal history for a provider"""
    signals = db.query(Signal).filter(
        Signal.provider_id == provider_id
    ).order_by(Signal.timestamp.desc()).limit(limit).all()
    
    return {
        "status": "success",
        "providerId": provider_id,
        "signals": [s.to_dict() for s in signals],
        "count": len(signals)
    }


# ============================================================
# WEBSOCKET ENDPOINTS
# ============================================================

@app.websocket("/ws/provider/{provider_id}")
async def provider_websocket(websocket: WebSocket, provider_id: str):
    """WebSocket endpoint for signal providers"""
    await ws_manager.connect_provider(websocket, provider_id)
    
    try:
        # Send connection confirmation
        await websocket.send_json({
            "action": "connected",
            "message": f"Successfully connected as provider {provider_id}"
        })
        
        # Listen for signals from provider
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("action") == "sendSignal":
                signal_data = message.get("data", {})
                
                # Broadcast to subscribers of THIS provider only
                await ws_manager.broadcast_to_provider_subscribers(
                    provider_id,
                    json.dumps({
                        "action": "sendSignal",
                        "providerId": provider_id,
                        "data": signal_data
                    })
                )
                
                logger.info(f"Signal from provider {provider_id}: {signal_data}")
                
                # Save signal to database
                try:
                    db = get_db_session(engine)
                    asset = signal_data.get('asset', 'UNKNOWN')
                    direction = signal_data.get('direction', 'UNKNOWN')
                    duration = int(signal_data.get('duration', 0))
                    brokers = json.dumps(signal_data.get('brokers', []))
                    signal = Signal(
                        provider_id=provider_id,
                        asset=asset,
                        direction=direction,
                        duration=duration,
                        brokers=brokers,
                        timestamp=datetime.utcnow()
                    )
                    db.add(signal)
                    db.commit()
                    db.close()
                except Exception as e:
                    logger.error(f"Error saving signal: {e}")
    
    except WebSocketDisconnect:
        ws_manager.disconnect_provider(websocket, provider_id)
    except Exception as e:
        logger.error(f"Provider WebSocket error: {e}")
        ws_manager.disconnect_provider(websocket, provider_id)


@app.websocket("/ws/subscriber")
async def subscriber_websocket(websocket: WebSocket):
    """WebSocket endpoint for signal subscribers"""
    await ws_manager.connect_subscriber(websocket)
    
    try:
        # Send connection confirmation
        await websocket.send_json({
            "action": "connected",
            "message": "Successfully connected as subscriber"
        })
        
        # Listen for commands from subscriber
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("action") == "joinProvider":
                provider_id = message.get("providerId")
                if provider_id:
                    ws_manager.join_provider(websocket, provider_id)
                    await websocket.send_json({
                        "action": "joinedProvider",
                        "providerId": provider_id,
                        "message": f"Joined provider {provider_id}"
                    })
            
            elif message.get("action") == "leaveProvider":
                provider_id = message.get("providerId")
                if provider_id:
                    ws_manager.leave_provider(websocket, provider_id)
                    await websocket.send_json({
                        "action": "leftProvider",
                        "providerId": provider_id,
                        "message": f"Left provider {provider_id}"
                    })
            
            elif message.get("action") == "ping":
                await websocket.send_json({"action": "pong"})
            
            elif message.get("action") == "sendSignal":
                provider_id = message.get("providerId")
                signal_data = message.get("data", {})
                
                if provider_id and signal_data:
                    # Broadcast to all subscribers of this provider
                    await ws_manager.broadcast_to_provider_subscribers(
                        provider_id,
                        json.dumps({
                            "action": "sendSignal",
                            "providerId": provider_id,
                            "data": signal_data
                        })
                    )
                    
                    logger.info(f"Signal relayed from subscriber WebSocket: provider={provider_id}, asset={signal_data.get('asset')}")
                    
                    # Save signal to database
                    try:
                        db = get_db_session(engine)
                        asset = signal_data.get('asset', 'UNKNOWN')
                        direction = signal_data.get('direction', 'UNKNOWN')
                        duration = int(signal_data.get('duration', 0))
                        brokers = json.dumps(signal_data.get('brokers', []))
                        signal = Signal(
                            provider_id=provider_id,
                            asset=asset,
                            direction=direction,
                            duration=duration,
                            brokers=brokers,
                            timestamp=datetime.utcnow()
                        )
                        db.add(signal)
                        db.commit()
                        db.close()
                    except Exception as e:
                        logger.error(f"Error saving signal: {e}")
    
    except WebSocketDisconnect:
        ws_manager.disconnect_subscriber(websocket)
    except Exception as e:
        logger.error(f"Subscriber WebSocket error: {e}")
        ws_manager.disconnect_subscriber(websocket)


# ============================================================
# HEALTH & INFO ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from sqlalchemy import text
    try:
        db = get_db_session(engine)
        db.execute(text("SELECT 1"))
        db_status = "connected"
        db.close()
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "disconnected"
    
    return {
        "status": "running",
        "database": db_status,
        "version": "2.0.0"
    }


@app.get("/")
async def root():
    """Serve web panel"""
    panel_path = Path(__file__).parent / "web_panel" / "index.html"
    if panel_path.exists():
        return FileResponse(str(panel_path))
    return {
        "message": "Z-BOT Signal Server with Database",
        "version": "2.0.0",
        "docs": "/docs",
        "panel": "/"
    }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    
    logger.info("=" * 60)
    logger.info("🚀 Z-BOT Signal Server with Database Starting...")
    logger.info(f"📍 Port: {port}")
    logger.info(f"📍 Database: {'PostgreSQL' if 'postgresql' in DATABASE_URL else 'SQLite'}")
    logger.info(f"📍 API Docs: http://localhost:{port}/docs")
    logger.info("=" * 60)
    
    uvicorn.run(
        "signal_server_api:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
