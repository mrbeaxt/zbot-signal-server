import json
import logging
from time import time
from typing import Dict, Union
from threading import Event, Lock
from websocket import WebSocketApp
from PySide6.QtCore import QObject, Signal
from decorators import run_in_thread
from models import UserInfo
from config.signal_server_config import SIGNAL_SERVER_URL, get_provider_ws_url, get_subscriber_ws_url
logger = logging.getLogger(__name__)

class SignalProviderManager(QObject):
    """
    Manages WebSocket communication with signal providers.
    Allows joining providers, sending signals, and emitting data via PySide6 signals.
    """
    data_received = Signal(dict)

    def __init__(self, user_info: UserInfo):
        super().__init__()
        self.user_info = user_info
        self.waiter = Event()
        self.id_name_map = {}
        self.enabled_provider_ids = set()
        self.real_access_pids = set()
        self._lock = Lock()
        self._running = False
        self._ws_started = False
        self.ws = None

    def get_ws(self) -> WebSocketApp:
        """Get WebSocket connection for subscriber with keepalive ping."""
        ws_url = get_subscriber_ws_url()
        logger.debug(f'Connecting to subscriber WebSocket: {ws_url}')
        return WebSocketApp(
            url=ws_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_close=self.on_close,
            on_error=self.on_error,
            ping_interval=30,        # Send ping every 30s to keep Render proxy connection alive
            ping_payload='{"action":"ping"}',
            ping_timeout=10           # Wait 10s for pong before considering connection dead
        )

    def add_provider(self, provider_id: str, provider_name: str, real_access: bool=False):
        """Add a provider to the id_name_map, auto-enable, and join it on WebSocket if connected."""
        with self._lock:
            # Store in map regardless of connection state
            self.id_name_map[provider_id] = provider_name
            if real_access:
                self.real_access_pids.add(provider_id)
            
            # Auto-enable provider so signals are received immediately
            self.enabled_provider_ids.add(provider_id)
            
            # Join provider if WebSocket is connected
            if self._ws_started and self.ws and self.ws.sock and self.ws.sock.connected:
                self._join_provider(provider_id=provider_id)

    def on_open(self, ws: WebSocketApp):
        """Send join messages when connection opens."""
        logger.info('WebSocket connection opened.')
        with self._lock:
            for provider_id in list(self.id_name_map.keys()):
                self._join_provider(provider_id=provider_id)

    def on_message(self, ws: WebSocketApp, message: str):
        """Process received WebSocket messages."""
        try:
            parsed_message = json.loads(message)
            logger.debug(f'Message received: {parsed_message}')
            if 'action' in parsed_message and parsed_message['action'] == 'sendSignal' and ('providerId' in parsed_message) and (parsed_message['providerId'] in self.enabled_provider_ids) and ('data' in parsed_message):
                timestamp = int(time())
                pid = parsed_message['providerId']
                data = {
                    'strategy': self.id_name_map.get(pid, 'Unknown'),
                    'timestamp': timestamp,
                    'asset': parsed_message['data'].get('asset', 'UNKNOWN'),
                    'dir': parsed_message['data'].get('direction', 'UNKNOWN'),
                    'timeframe': parsed_message['data'].get('duration', 0),
                    'brokers': parsed_message['data'].get('brokers', []),
                    'real_access': pid in self.real_access_pids
                }
                self.data_received.emit(data)
                logger.debug(f'self.real_access_pids={self.real_access_pids!r}, data={data!r}')
        except Exception as e:
            logger.error(f'Error processing WebSocket message: {e}')

    def _join_provider(self, provider_id: str):
        """Join a provider via WebSocket."""
        join_message = {'action': 'joinProvider', 'providerId': provider_id}
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(join_message))
                logger.info(f'Sent join request for provider ID: {provider_id}')
        except Exception as e:
            logger.error(f'Failed to join provider {provider_id}: {e}')

    def disable_provider(self, provider_id: str):
        """Disable a provider, preventing signal reception."""
        self.enabled_provider_ids.discard(provider_id)
        logger.debug(f'Disabled provider: {provider_id}')

    def enable_provider(self, provider_id: str):
        """Enable a provider, allowing signal reception."""
        self.enabled_provider_ids.add(provider_id)
        logger.debug(f'Enabled provider: {provider_id}')

    def send_signal(self, provider_id: str, signal_data: Dict[str, Union[str, int]]):
        """Send a signal to a specific provider."""
        if provider_id not in self.id_name_map:
            logger.debug(f'Provider {provider_id} not in id_name_map, skipping signal.')
            return
        signal_message = {'action': 'sendSignal', 'providerId': provider_id, 'data': signal_data}
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(signal_message))
                logger.info(f'Sent signal to provider ID {provider_id}: {signal_data}')
            else:
                logger.debug(f'WebSocket not connected, signal queued for provider {provider_id}')
        except Exception as e:
            logger.error(f'Failed to send signal to provider {provider_id}: {e}')

    @run_in_thread
    def start(self) -> None:
        """Start WebSocket connection (non-blocking, only starts once)."""
        with self._lock:
            if self._running:
                logger.debug('WebSocket already running, skipping start.')
                return
            self._running = True
        
        logger.info('Starting WebSocket connection...')
        while not self.waiter.wait(10):
            try:
                self.ws = self.get_ws()
                self._ws_started = True
                self.ws.run_forever()
            except Exception as e:
                logger.error(f'WebSocket error: {e}')
            finally:
                self._ws_started = False
                try:
                    if self.ws:
                        self.ws.close()
                except:
                    pass
        self._running = False
        self.waiter.clear()

    def on_close(self, ws: WebSocketApp, close_status_code: int, close_msg: str):
        """
        Handle WebSocket disconnection — force reconnection loop to rebuild.
        """
        logger.warning(f'WebSocket closed (code={close_status_code}): {close_msg}')
        with self._lock:
            self._ws_started = False
        # Force run_forever() to return by ensuring no lingering state
        try:
            if self.ws:
                self.ws.keep_running = False
        except Exception:
            pass

    def on_error(self, ws: WebSocketApp, error: Exception):
        """
        Handle WebSocket errors — log and let run_forever() handle reconnection.
        """
        err_str = str(error)
        # Suppress noisy connection-refused logs during normal reconnect cycling
        if 'Connection refused' not in err_str and '10061' not in err_str:
            logger.error(f'WebSocket error: {error}')

    def close(self) -> None:
        """Close the WebSocket connection safely."""
        self.waiter.set()
        with self._lock:
            self._running = False
            self._ws_started = False
        try:
            if self.ws:
                self.ws.close()
        except:
            pass
        logger.info('WebSocket connection closed.')
