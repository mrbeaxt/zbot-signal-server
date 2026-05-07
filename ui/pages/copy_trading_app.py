# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: ui\pages\copy_trading_app.py
# Bytecode version: 3.10.0rc2 (3439)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

from typing import Any
from PySide6.QtWidgets import QWidget, QMessageBox
from PySide6.QtCore import Qt
from models import UserInfo
from ui.common.custom_widgets import CustomVBox, Icon, Button, GroupBox, CLabel, create_back_button, LineEdit
from ui.pages.all_providers import AllProvidersWidget
from ui.pages.my_provider_subscriptions import MyProviderSubscriptionsWidget
from ui.pages.my_providers import MyProvidersWidget
from translation.translator import tr
import json
import os

class CopyTradingApp(QWidget):

    def __init__(self, db: dict[str, Any], user_info: UserInfo):
        super().__init__()
        self.db = db
        self.user_info = user_info
        self.lang = db.get('lang', 'en')
        self.setWindowTitle(tr('Copy Trading System', self.lang))
        self.setGeometry(100, 100, 800, 600)
        self.setWindowIcon(Icon())
        
        # Token configuration section
        self.token_label = CLabel(
            text=tr('🔑 API Token (Get from: https://zbot-signal-server.onrender.com)', self.lang),
            alignment=Qt.AlignmentFlag.AlignLeft,
            min_height=20
        )
        self.token_input = LineEdit()
        self.token_input.setPlaceholderText(tr('Paste your token here...', self.lang))
        self.token_input.setMinimumWidth(400)
        
        # Load saved token
        self.load_token()
        
        self.save_token_btn = Button(
            text=tr('💾 Save Token', self.lang),
            callback=self.save_token,
            icon_name='document-save-24'
        )
        
        self.token_group = GroupBox(
            title=tr('Server Connection', self.lang),
            layout=CustomVBox(self.token_label, self.token_input, self.save_token_btn)
        )
        
        self.page_title_label = CLabel(text=tr('Manage Copy Trade Signals: Providers, Subscriptions and Settings', self.lang), alignment=Qt.AlignmentFlag.AlignCenter, min_height=0)
        self.page_title_label.setProperty('title', True)
        self.all_providers_tab = AllProvidersWidget(back_button=create_back_button(lang=self.lang, callback=self.back), lang=self.lang, user_info=user_info)
        self.my_subscriptions_tab = MyProviderSubscriptionsWidget(back_button=create_back_button(lang=self.lang, callback=self.back), lang=self.lang, user_info=user_info)
        self.my_providers_tab = MyProvidersWidget(back_button=create_back_button(lang=self.lang, callback=self.back), lang=self.lang, user_info=user_info)
        all_providers_button = Button(text=tr('View all available signal providers and choose between operating on real account or testing on demo account', self.lang), callback=self.show_all_providers, icon_name='go-24')
        my_subscriptions_button = Button(text=tr('Manage my signal subscriptions, view status, cancel or renew', self.lang), callback=self.show_my_subscriptions, icon_name='go-24')
        my_providers_button = Button(text=tr('Manage my signal providers, create, edit, view subscribers and requests', self.lang), callback=self.show_my_providers, icon_name='go-24')
        self.all_providers_tab.hide()
        self.my_subscriptions_tab.hide()
        self.my_providers_tab.hide()
        self.group_box = GroupBox(title=tr('What Are You Looking For?', self.lang), layout=CustomVBox(all_providers_button, my_subscriptions_button, my_providers_button))
        self.main_layout = CustomVBox(self.token_group, self.page_title_label, self.group_box, self.all_providers_tab, self.my_subscriptions_tab, self.my_providers_tab)
        self.setLayout(self.main_layout)

    def show_all_providers(self):
        self.goto(self.all_providers_tab)

    def show_my_subscriptions(self):
        self.goto(self.my_subscriptions_tab)

    def show_my_providers(self):
        self.goto(self.my_providers_tab)

    def reset(self):
        self.all_providers_tab.hide()
        self.my_subscriptions_tab.hide()
        self.my_providers_tab.hide()

    def goto(self, tab):
        self.reset()
        self.group_box.hide()
        self.page_title_label.hide()
        tab.show()

    def back(self):
        self.reset()
        self.group_box.show()
        self.page_title_label.show()
    
    def save_token(self):
        """Save API token to local file"""
        token = self.token_input.text().strip()
        if not token:
            QMessageBox.warning(self, tr('Error', self.lang), tr('Please enter a token!', self.lang))
            return
        
        # Save to copy_trading_token.json
        token_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'copy_trading_token.json')
        try:
            with open(token_file, 'w') as f:
                json.dump({'token': token}, f)
            
            # Also update user_info
            self.user_info.access_token = token
            
            QMessageBox.information(
                self, 
                tr('Success', self.lang), 
                tr('✅ Token saved successfully!\n\nYou can now use copy trading features.', self.lang)
            )
        except Exception as e:
            QMessageBox.critical(self, tr('Error', self.lang), f'{tr("Failed to save token:", self.lang)} {str(e)}')
    
    def load_token(self):
        """Load saved API token from file"""
        token_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'copy_trading_token.json')
        try:
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    data = json.load(f)
                    token = data.get('token', '')
                    if token:
                        self.token_input.setText(token)
                        self.user_info.access_token = token
        except:
            pass