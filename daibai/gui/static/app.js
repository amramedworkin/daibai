/**
 * DaiBai GUI - Main Application JavaScript
 */

// Supported LLM providers (order for display)
const SUPPORTED_LLM_PROVIDERS = [
    'ollama', 'openai', 'anthropic', 'gemini', 'azure',
    'groq', 'deepseek', 'mistral', 'nvidia', 'alibaba', 'meta'
];

// UI Templates for settings modal (switch/case logic)
const LLM_TEMPLATES = {
    ollama: { label: 'Ollama', fields: ['endpoint', 'model'], endpointDefault: 'http://localhost:11434', needsApiKey: false },
    openai: { label: 'OpenAI', fields: ['api_key', 'model'], needsApiKey: true },
    anthropic: { label: 'Anthropic', fields: ['api_key', 'model'], needsApiKey: true },
    gemini: { label: 'Google Gemini', fields: ['api_key', 'model'], needsApiKey: true },
    azure: { label: 'Azure OpenAI', fields: ['api_key', 'endpoint', 'deployment'], needsApiKey: true },
    groq: { label: 'Groq', fields: ['api_key', 'model'], needsApiKey: true },
    deepseek: { label: 'DeepSeek', fields: ['api_key', 'model'], needsApiKey: true },
    mistral: { label: 'Mistral AI', fields: ['api_key', 'model'], needsApiKey: true },
    nvidia: { label: 'Nvidia NIM', fields: ['api_key', 'model'], needsApiKey: true },
    alibaba: { label: 'Alibaba Cloud', fields: ['api_key', 'model', 'endpoint'], needsApiKey: true, endpointDefault: 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1' },
    meta: { label: 'Meta (Llama)', fields: ['api_key', 'model', 'endpoint'], needsApiKey: true }
};

const DB_TEMPLATES = {
    mysql: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 3306 },
    postgres: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 5432 },
    oracle: { fields: ['host', 'port', 'service_name', 'user', 'password'], defaultPort: 1521 },
    sqlserver: { fields: ['host', 'port', 'user', 'password', 'database'], defaultPort: 1433 }
};

const CLOUD_PROVIDERS = {
    aws: { fields: ['region', 'secret_arn'] },
    azure: { fields: ['region', 'instance'] }
};

class DaiBaiApp {
    constructor() {
        this.conversationId = null;
        this.ws = null;
        this.isLoading = false;
        this.lastGeneratedSql = null;
        this.resultsCache = {};  // resultsId -> results for Export CSV
        this.sessionMessages = [];  // messages in current conversation for prompts list
        this.attachedFiles = [];   // [{ id, name, size }] for file upload
        
        this.init();
    }
    
    async init() {
        this.bindElements();
        this.loadPreferences();
        this.bindEvents();
        await this.loadSettings();
        await this.loadConversations();
        this.connectWebSocket();
    }
    
    loadPreferences() {
        const prefs = JSON.parse(localStorage.getItem('daibai_preferences') || '{}');
        
        // Auto-copy defaults to true
        this.autoCopyCheckbox.checked = prefs.autoCopy !== false;
        
        // Auto-CSV defaults to false
        this.autoCsvCheckbox.checked = prefs.autoCsv === true;
        
        // Execute checkbox
        this.executeCheckbox.checked = prefs.autoExecute === true;
        
        // Sidebar state
        if (prefs.sidebarCollapsed) {
            this.sidebar.classList.add('collapsed');
        }
    }
    
    savePreferences() {
        const prefs = {
            autoCopy: this.autoCopyCheckbox.checked,
            autoCsv: this.autoCsvCheckbox.checked,
            autoExecute: this.executeCheckbox.checked,
            sidebarCollapsed: this.sidebar.classList.contains('collapsed'),
            database: this.databaseSelect.value,
            llm: this.llmSelect.value,
            mode: this.modeSelect.value
        };
        localStorage.setItem('daibai_preferences', JSON.stringify(prefs));
    }
    
    async handleFileSelect(e) {
        const files = Array.from(e.target.files || []);
        e.target.value = '';
        for (const file of files) {
            try {
                const formData = new FormData();
                formData.append('file', file);
                const res = await fetch('/api/upload', { method: 'POST', body: formData });
                if (res.ok) {
                    const data = await res.json();
                    this.attachedFiles.push({ id: data.id, name: data.name, size: data.size });
                    this.renderAttachedFiles();
                } else {
                    const err = await res.json();
                    alert('Upload failed: ' + (err.detail || 'Unknown error'));
                }
            } catch (err) {
                alert('Upload failed: ' + (err.message || 'Network error'));
            }
        }
    }
    
    removeAttachedFile(id) {
        this.attachedFiles = this.attachedFiles.filter(f => f.id !== id);
        this.renderAttachedFiles();
    }
    
    renderAttachedFiles() {
        if (!this.attachedFilesEl) return;
        if (this.attachedFiles.length === 0) {
            this.attachedFilesEl.innerHTML = '';
            this.attachedFilesEl.style.display = 'none';
            return;
        }
        this.attachedFilesEl.style.display = 'flex';
        this.attachedFilesEl.innerHTML = this.attachedFiles.map(f => `
            <span class="file-chip" data-id="${f.id}">
                <span class="file-chip-name">${this.escapeHtml(f.name)}</span>
                <button class="file-chip-remove" data-id="${f.id}" title="Remove">×</button>
            </span>
        `).join('');
        this.attachedFilesEl.querySelectorAll('.file-chip-remove').forEach(btn => {
            btn.addEventListener('click', () => this.removeAttachedFile(btn.dataset.id));
        });
    }
    
    copyToClipboard(text) {
        if (text && this.autoCopyCheckbox.checked) {
            navigator.clipboard.writeText(text).catch(err => {
                console.error('Failed to copy:', err);
            });
        }
    }
    
    saveToCsv(results) {
        if (!results || results.length === 0 || !this.autoCsvCheckbox.checked) {
            return;
        }
        
        // Generate CSV content
        const columns = Object.keys(results[0]);
        const csvRows = [];
        
        // Header row
        csvRows.push(columns.map(col => `"${col}"`).join(','));
        
        // Data rows
        for (const row of results) {
            const values = columns.map(col => {
                const val = row[col];
                if (val === null || val === undefined) return '';
                const str = String(val).replace(/"/g, '""');
                return `"${str}"`;
            });
            csvRows.push(values.join(','));
        }
        
        const csvContent = csvRows.join('\n');
        
        // Generate filename with timestamp
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const filename = `daibai_results_${timestamp}.csv`;
        
        // Download file
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        link.click();
        URL.revokeObjectURL(link.href);
    }
    
    bindElements() {
        // Navigation
        this.sidebarToggle = document.getElementById('sidebarToggle');
        this.sidebar = document.getElementById('sidebar');
        this.databaseSelect = document.getElementById('databaseSelect');
        this.llmSelect = document.getElementById('llmSelect');
        this.modeSelect = document.getElementById('modeSelect');
        this.autoCopyCheckbox = document.getElementById('autoCopyCheckbox');
        this.autoCsvCheckbox = document.getElementById('autoCsvCheckbox');
        this.schemaBtn = document.getElementById('schemaBtn');
        this.schemaModal = document.getElementById('schemaModal');
        this.schemaModalClose = document.getElementById('schemaModalClose');
        this.schemaContent = document.getElementById('schemaContent');
        this.settingsBtn = document.getElementById('settingsBtn');
        this.settingsModal = document.getElementById('settingsModal');
        this.settingsModalClose = document.getElementById('settingsModalClose');
        this.settingsContent = document.getElementById('settingsContent');
        this.settingsSave = document.getElementById('settingsSave');
        this.settingsCancel = document.getElementById('settingsCancel');
        
        // Sidebar
        this.newChatBtn = document.getElementById('newChatBtn');
        this.conversationList = document.getElementById('conversationList');
        
        // Chat
        this.messagesContainer = document.getElementById('messagesContainer');
        this.welcomeMessage = document.getElementById('welcomeMessage');
        this.promptInput = document.getElementById('promptInput');
        this.sendBtn = document.getElementById('sendBtn');
        this.executeCheckbox = document.getElementById('executeCheckbox');
        this.attachBtn = document.getElementById('attachBtn');
        this.fileInput = document.getElementById('fileInput');
        this.attachedFilesEl = document.getElementById('attachedFiles');
    }
    
    bindEvents() {
        // Sidebar toggle
        this.sidebarToggle.addEventListener('click', () => {
            this.sidebar.classList.toggle('collapsed');
            this.savePreferences();
        });
        
        // Settings changes
        this.databaseSelect.addEventListener('change', () => {
            this.updateSettings();
            this.savePreferences();
        });
        this.llmSelect.addEventListener('change', () => {
            this.updateSettings();
            this.savePreferences();
        });
        this.modeSelect.addEventListener('change', () => this.savePreferences());
        this.autoCopyCheckbox.addEventListener('change', () => this.savePreferences());
        this.autoCsvCheckbox.addEventListener('change', () => this.savePreferences());
        this.executeCheckbox.addEventListener('change', () => this.savePreferences());
        
        // Schema modal
        this.schemaBtn.addEventListener('click', () => this.showSchema());
        this.schemaModalClose.addEventListener('click', () => {
            this.schemaModal.classList.remove('active');
        });
        this.schemaModal.addEventListener('click', (e) => {
            if (e.target === this.schemaModal) {
                this.schemaModal.classList.remove('active');
            }
        });

        // Settings modal
        if (this.settingsBtn) {
            this.settingsBtn.addEventListener('click', (e) => {
                e.preventDefault();
                this.showSettings();
            });
        }
        this.settingsModalClose.addEventListener('click', () => this.closeSettings());
        this.settingsCancel.addEventListener('click', () => this.closeSettings());
        this.settingsSave.addEventListener('click', () => this.saveSettings());
        this.settingsModal.addEventListener('click', (e) => {
            if (e.target === this.settingsModal) this.closeSettings();
            const navItem = e.target.closest('.settings-nav-item');
            if (navItem && this.settingsActiveTab === 'llm_providers') {
                const provider = navItem.dataset.provider;
                if (!provider) return;
                const main = document.getElementById('settingsLLMMain');
                const selected = this.settingsState?.selected_llm_provider;
                if (selected && main) {
                    this.settingsState.llm_providers = this.settingsState.llm_providers || {};
                    this.settingsState.llm_providers[selected] = this.readLLMFormValues();
                }
                this.settingsState.selected_llm_provider = provider;
                this.settingsModal.querySelectorAll('.settings-nav-item').forEach(n => n.classList.toggle('active', n.dataset.provider === provider));
                if (main) main.innerHTML = this.renderLLMProviderForm(provider, this.settingsState?.llm_providers?.[provider] || {});
                this.bindSettingsDynamicHandlers();
            }
        });
        this.settingsModal.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchSettingsTab(tab.dataset.tab));
        });
        
        // New chat
        this.newChatBtn.addEventListener('click', () => this.startNewChat());
        
        // Input handling
        this.promptInput.addEventListener('input', () => {
            this.autoResizeTextarea();
            this.updateSendButton();
        });
        
        this.promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        
        if (this.attachBtn && this.fileInput) {
            this.attachBtn.addEventListener('click', () => this.fileInput.click());
            this.fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
        }
        
        // Example prompts
        document.querySelectorAll('.example-prompt').forEach(btn => {
            btn.addEventListener('click', () => {
                this.promptInput.value = btn.dataset.prompt;
                this.updateSendButton();
                this.sendMessage();
            });
        });
    }
    
    autoResizeTextarea() {
        const textarea = this.promptInput;
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    }
    
    updateSendButton() {
        this.sendBtn.disabled = !this.promptInput.value.trim() || this.isLoading;
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();
            const prefs = JSON.parse(localStorage.getItem('daibai_preferences') || '{}');
            
            // Populate database dropdown
            const savedDb = prefs.database && settings.databases.includes(prefs.database) 
                ? prefs.database : settings.current_database;
            this.databaseSelect.innerHTML = settings.databases
                .map(db => `<option value="${db}" ${db === savedDb ? 'selected' : ''}>${db}</option>`)
                .join('');
            
            // Populate LLM dropdown
            const savedLlm = prefs.llm && settings.llm_providers.includes(prefs.llm)
                ? prefs.llm : settings.current_llm;
            this.llmSelect.innerHTML = settings.llm_providers
                .map(llm => `<option value="${llm}" ${llm === savedLlm ? 'selected' : ''}>${llm}</option>`)
                .join('');
            
            // Set mode from preferences or default
            this.modeSelect.value = prefs.mode || settings.current_mode || 'sql';
            
            // Always sync settings to server to ensure database/LLM is correct
            await this.updateSettings();
        } catch (error) {
            console.error('Failed to load settings:', error);
        }
    }
    
    async updateSettings() {
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    database: this.databaseSelect.value,
                    llm: this.llmSelect.value,
                    mode: this.modeSelect.value
                })
            });
        } catch (error) {
            console.error('Failed to update settings:', error);
        }
    }
    
    async loadConversations() {
        try {
            const response = await fetch('/api/conversations');
            const conversations = await response.json();
            
            this.conversationList.innerHTML = conversations.map(conv => `
                <div class="conversation-item ${conv.id === this.conversationId ? 'active' : ''}" data-id="${conv.id}">
                    <span class="title">${this.escapeHtml(conv.title)}</span>
                    <button class="delete-btn" data-id="${conv.id}">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
            `).join('');
            
            // Bind conversation click handlers
            this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (!e.target.closest('.delete-btn')) {
                        this.loadConversation(item.dataset.id);
                    }
                });
            });
            
            // Bind delete handlers
            this.conversationList.querySelectorAll('.delete-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.deleteConversation(btn.dataset.id);
                });
            });
        } catch (error) {
            console.error('Failed to load conversations:', error);
        }
    }
    
    async loadConversation(id) {
        try {
            const response = await fetch(`/api/conversations/${id}`);
            const conversation = await response.json();
            
            this.conversationId = id;
            this.sessionMessages = conversation.messages || [];
            this.welcomeMessage.style.display = 'none';
            
            // Clear and render messages
            this.messagesContainer.innerHTML = '';
            this.sessionMessages.forEach((msg, i) => {
                this.renderMessage(msg, i);
            });
            
            // Update prompts list in sidebar
            this.updatePromptsList(conversation.messages);
            
            // Update sidebar
            this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
                item.classList.toggle('active', item.dataset.id === id);
            });
            
            this.scrollToBottom();
        } catch (error) {
            console.error('Failed to load conversation:', error);
        }
    }
    
    async deleteConversation(id) {
        try {
            await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
            
            if (id === this.conversationId) {
                this.startNewChat();
            }
            
            await this.loadConversations();
        } catch (error) {
            console.error('Failed to delete conversation:', error);
        }
    }
    
    startNewChat() {
        this.conversationId = null;
        this.sessionMessages = [];
        this.messagesContainer.innerHTML = '';
        this.messagesContainer.appendChild(this.welcomeMessage);
        this.welcomeMessage.style.display = 'flex';
        this.updatePromptsList([]);
        
        this.conversationList.querySelectorAll('.conversation-item').forEach(item => {
            item.classList.remove('active');
        });
    }
    
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/chat`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
        };
        
        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleWebSocketMessage(data);
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket disconnected, reconnecting...');
            setTimeout(() => this.connectWebSocket(), 3000);
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'ack':
                this.conversationId = data.conversation_id;
                break;
                
            case 'sql':
                this.removeLoadingIndicator();
                this.lastGeneratedSql = data.content;
                const assistantMsg = { role: 'assistant', content: data.content, sql: data.content, timestamp: new Date().toISOString() };
                this.sessionMessages.push(assistantMsg);
                this.renderAssistantMessage(data.content, null, this.sessionMessages.length - 1);
                this.copyToClipboard(data.content);
                this.updatePromptsList(this.sessionMessages);
                break;
                
            case 'results':
                this.appendResultsToLastMessage(data);
                this.saveToCsv(data.content);
                if (this.sessionMessages.length > 0) {
                    const last = this.sessionMessages[this.sessionMessages.length - 1];
                    if (last.role === 'assistant') last.results = data.content;
                }
                break;
                
            case 'error':
                this.removeLoadingIndicator();
                this.renderErrorMessage(data.content);
                break;
                
            case 'done':
                this.isLoading = false;
                this.updateSendButton();
                this.updatePromptsList(this.sessionMessages);
                this.loadConversations();
                break;
        }
    }
    
    async sendMessage() {
        const query = this.promptInput.value.trim();
        if (!query || this.isLoading) return;
        
        this.isLoading = true;
        this.updateSendButton();
        
        // Hide welcome message
        this.welcomeMessage.style.display = 'none';
        
        // Render user message
        const userMsg = { role: 'user', content: query, timestamp: new Date().toISOString() };
        this.sessionMessages.push(userMsg);
        this.renderMessage(userMsg, this.sessionMessages.length - 1);
        
        // Clear input and attached files
        this.promptInput.value = '';
        this.attachedFiles = [];
        this.renderAttachedFiles();
        this.autoResizeTextarea();
        
        // Show loading indicator
        this.showLoadingIndicator();
        
        // Send via WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                query: query,
                conversation_id: this.conversationId,
                execute: this.executeCheckbox.checked
            }));
        } else {
            // Fallback to REST API
            await this.sendMessageRest(query);
        }
        
        this.scrollToBottom();
    }
    
    async sendMessageRest(query) {
        try {
            const response = await fetch('/api/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: query,
                    conversation_id: this.conversationId,
                    execute: this.executeCheckbox.checked
                })
            });
            
            const data = await response.json();
            
            this.removeLoadingIndicator();
            this.conversationId = data.conversation_id;
            this.lastGeneratedSql = data.sql;
            
            const assistantMsg = { role: 'assistant', content: data.sql, sql: data.sql, results: data.results, timestamp: new Date().toISOString() };
            this.sessionMessages.push(assistantMsg);
            this.renderAssistantMessage(data.sql, data.results, this.sessionMessages.length - 1);
            this.copyToClipboard(data.sql);
            this.saveToCsv(data.results);
            this.updatePromptsList(this.sessionMessages);
            
            await this.loadConversations();
        } catch (error) {
            this.removeLoadingIndicator();
            this.renderErrorMessage(error.message);
        } finally {
            this.isLoading = false;
            this.updateSendButton();
        }
    }
    
    updatePromptsList(messages) {
        const section = document.getElementById('promptsSection');
        const list = document.getElementById('promptsList');
        if (!section || !list) return;
        
        const msgs = messages || [];
        const userIndices = msgs.map((m, i) => m.role === 'user' ? i : -1).filter(i => i >= 0);
        if (userIndices.length === 0) {
            section.style.display = 'none';
            return;
        }
        
        section.style.display = 'block';
        list.innerHTML = userIndices.map(idx => {
            const m = msgs[idx];
            const title = (m.content || '').slice(0, 50) + ((m.content || '').length > 50 ? '...' : '');
            return `<div class="prompt-item" data-msg-index="${idx}" title="${this.escapeHtml(m.content || '')}">${this.escapeHtml(title)}</div>`;
        }).join('');
        
        list.querySelectorAll('.prompt-item').forEach(el => {
            el.addEventListener('click', () => {
                const target = this.messagesContainer.querySelector(`[data-msg-index="${el.dataset.msgIndex}"]`);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    list.querySelectorAll('.prompt-item').forEach(p => p.classList.remove('active'));
                    el.classList.add('active');
                }
            });
        });
    }
    
    renderMessage(msg, msgIndex = -1) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${msg.role}`;
        if (msgIndex >= 0) messageEl.dataset.msgIndex = msgIndex;
        
        const avatar = msg.role === 'user' ? 'U' : 'D';
        const roleName = msg.role === 'user' ? 'You' : 'DaiBai';
        const time = this.formatTime(msg.timestamp);
        
        if (msg.role === 'user') {
            messageEl.innerHTML = `
                <div class="message-avatar">${avatar}</div>
                <div class="message-content">
                    <div class="message-header">
                        <span class="message-role">${roleName}</span>
                        <span class="message-time">${time}</span>
                    </div>
                    <div class="message-text">${this.escapeHtml(msg.content)}</div>
                </div>
            `;
        } else {
            messageEl.innerHTML = `
                <div class="message-avatar">${avatar}</div>
                <div class="message-content">
                    <div class="message-header">
                        <span class="message-role">${roleName}</span>
                        <span class="message-time">${time}</span>
                    </div>
                    ${this.renderSqlBlock(msg.sql || msg.content)}
                    ${msg.results ? this.renderResults(msg.results) : ''}
                </div>
            `;
        }
        
        this.messagesContainer.appendChild(messageEl);
        if (msg.role === 'assistant' && msg.results) {
            this.bindExportCsvButtons(messageEl);
        }
        this.scrollToBottom();
    }
    
    renderAssistantMessage(sql, results = null, msgIndex = -1) {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        messageEl.id = 'lastAssistantMessage';
        if (msgIndex >= 0) messageEl.dataset.msgIndex = msgIndex;
        
        messageEl.innerHTML = `
            <div class="message-avatar">D</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-role">DaiBai</span>
                    <span class="message-time">${this.formatTime(new Date().toISOString())}</span>
                </div>
                ${this.renderSqlBlock(sql)}
                ${results ? this.renderResults(results) : ''}
            </div>
        `;
        
        this.messagesContainer.appendChild(messageEl);
        this.scrollToBottom();
        
        // Bind copy, run, and export buttons
        this.bindSqlActions(messageEl, sql);
        this.bindExportCsvButtons(messageEl);
    }
    
    renderSqlBlock(sql) {
        if (!sql) return '<div class="message-text">Could not generate SQL</div>';
        
        return `
            <div class="sql-block">
                <div class="sql-header">
                    <span>SQL</span>
                    <div class="sql-actions">
                        <button class="copy-btn">Copy</button>
                        <button class="run-btn">Run</button>
                    </div>
                </div>
                <pre class="sql-code">${this.escapeHtml(sql)}</pre>
            </div>
        `;
    }
    
    renderResults(results) {
        if (!results || results.length === 0) {
            return '<div class="results-container"><p>No results</p></div>';
        }
        
        const columns = Object.keys(results[0]);
        const resultsId = 'results-' + Date.now() + '-' + Math.random().toString(36).slice(2);
        this.resultsCache[resultsId] = results;
        
        return `
            <div class="results-container" data-results-id="${resultsId}">
                <div class="results-header">
                    <span>${results.length} row(s) returned</span>
                    <button class="export-csv-btn" data-results-id="${resultsId}" title="Export to CSV">Export CSV</button>
                </div>
                <table class="results-table">
                    <thead>
                        <tr>${columns.map(col => `<th>${this.escapeHtml(col)}</th>`).join('')}</tr>
                    </thead>
                    <tbody>
                        ${results.slice(0, 100).map(row => `
                            <tr>${columns.map(col => `<td>${this.escapeHtml(String(row[col] ?? ''))}</td>`).join('')}</tr>
                        `).join('')}
                    </tbody>
                </table>
                ${results.length > 100 ? `<p style="margin-top: 8px; color: var(--text-muted);">Showing first 100 of ${results.length} rows</p>` : ''}
            </div>
        `;
    }
    
    appendResultsToLastMessage(data) {
        const lastMessage = document.getElementById('lastAssistantMessage');
        if (lastMessage) {
            const content = lastMessage.querySelector('.message-content');
            const resultsHtml = this.renderResults(data.content);
            content.insertAdjacentHTML('beforeend', resultsHtml);
            this.bindExportCsvButtons(lastMessage);
            this.scrollToBottom();
        }
    }
    
    bindExportCsvButtons(container) {
        (container || this.messagesContainer).querySelectorAll('.export-csv-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const resultsId = btn.dataset.resultsId;
                const results = this.resultsCache[resultsId];
                if (results && results.length > 0) {
                    this.saveToCsv(results);
                    btn.textContent = 'Saved!';
                    setTimeout(() => { btn.textContent = 'Export CSV'; }, 2000);
                }
            });
        });
    }
    
    bindSqlActions(messageEl, sql) {
        const copyBtn = messageEl.querySelector('.copy-btn');
        const runBtn = messageEl.querySelector('.run-btn');
        
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(sql);
                copyBtn.textContent = 'Copied!';
                setTimeout(() => copyBtn.textContent = 'Copy', 2000);
            });
        }
        
        if (runBtn) {
            runBtn.addEventListener('click', async () => {
                runBtn.textContent = 'Running...';
                runBtn.disabled = true;
                
                try {
                    const response = await fetch('/api/execute', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sql: sql })
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        this.appendResultsToLastMessage({ content: data.results });
                        this.saveToCsv(data.results);
                    } else {
                        this.renderErrorMessage(data.detail || 'Execution failed');
                    }
                } catch (error) {
                    this.renderErrorMessage(error.message);
                } finally {
                    runBtn.textContent = 'Run';
                    runBtn.disabled = false;
                }
            });
        }
    }
    
    renderErrorMessage(error) {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        
        // Extract error message from various formats
        let errorMsg = 'Unknown error';
        if (typeof error === 'string') {
            errorMsg = error;
        } else if (error && error.message) {
            errorMsg = error.message;
        } else if (error && error.detail) {
            errorMsg = error.detail;
        } else if (error && typeof error === 'object') {
            errorMsg = JSON.stringify(error);
        }
        
        messageEl.innerHTML = `
            <div class="message-avatar" style="background: var(--error);">!</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-role">Error</span>
                </div>
                <div class="message-text" style="color: var(--error);">${this.escapeHtml(errorMsg)}</div>
            </div>
        `;
        
        this.messagesContainer.appendChild(messageEl);
        this.scrollToBottom();
    }
    
    showLoadingIndicator() {
        const loadingEl = document.createElement('div');
        loadingEl.className = 'message assistant loading-message';
        loadingEl.innerHTML = `
            <div class="message-avatar">D</div>
            <div class="loading">
                <div class="loading-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
                <span>Generating...</span>
            </div>
        `;
        this.messagesContainer.appendChild(loadingEl);
        this.scrollToBottom();
    }
    
    removeLoadingIndicator() {
        const loading = this.messagesContainer.querySelector('.loading-message');
        if (loading) {
            loading.remove();
        }
    }
    
    async showSchema() {
        this.schemaModal.classList.add('active');
        this.schemaContent.textContent = 'Loading schema...';
        
        try {
            const response = await fetch('/api/schema');
            const data = await response.json();
            this.schemaContent.textContent = data.schema || 'No schema available';
        } catch (error) {
            this.schemaContent.textContent = 'Failed to load schema: ' + error.message;
        }
    }

    async showSettings() {
        if (!this.settingsModal) return;
        this.settingsActiveTab = this.settingsActiveTab || 'account';
        try {
            this.settingsState = await this.loadSettingsState();
        } catch (e) {
            this.settingsState = {};
        }
        this.settingsModal.classList.add('active');
        this.renderSettingsContent(this.settingsActiveTab);
        this.settingsModal.querySelectorAll('.settings-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === this.settingsActiveTab);
        });
    }

    async loadSettingsState() {
        try {
            const [settingsRes, prefs] = await Promise.all([
                fetch('/api/settings'),
                Promise.resolve(JSON.parse(localStorage.getItem('daibai_preferences') || '{}'))
            ]);
            const settings = await settingsRes.json();
            const configured = settings.llm_providers || [];
            const apiConfigs = settings.llm_provider_configs || {};
            const llm_providers = {};
            for (const p of SUPPORTED_LLM_PROVIDERS) {
                const c = apiConfigs[p];
                llm_providers[p] = c ? { api_key: c.api_key, model: c.model, endpoint: c.endpoint, deployment: c.deployment } : {};
            }
            return {
                account: { email: '', user_id: '', plan: 'Free' },
                llm: { provider: settings.current_llm || 'gemini' },
                configured_llm_providers: configured,
                llm_providers,
                selected_llm_provider: configured[0] || SUPPORTED_LLM_PROVIDERS[0],
                databases: { type: 'mysql', hostType: 'local', current: settings.current_database },
                data_privacy: { save_history: true, query_caching: false },
                preferences: { theme: 'system', auto_charts: false, ...prefs }
            };
        } catch (e) {
            return {};
        }
    }

    closeSettings() {
        this.settingsModal.classList.remove('active');
    }

    switchSettingsTab(tabId) {
        this.settingsActiveTab = tabId;
        this.settingsModal.querySelectorAll('.settings-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === tabId);
        });
        this.renderSettingsContent(tabId);
    }

    renderSettingsContent(tabId) {
        const content = this.settingsContent;
        switch (tabId) {
            case 'account':
                content.innerHTML = this.renderAccountTab();
                break;
            case 'llm_providers':
                content.innerHTML = this.renderLLMProvidersTab();
                break;
            case 'databases':
                content.innerHTML = this.renderDatabaseConnectionsTab();
                break;
            case 'data':
                content.innerHTML = this.renderDataPrivacyTab();
                break;
            case 'preferences':
                content.innerHTML = this.renderPreferencesTab();
                break;
            default:
                content.innerHTML = '';
        }
        this.bindSettingsDynamicHandlers();
    }

    renderAccountTab() {
        const auth = this.settingsState?.account || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">User Identity</div>
                <div class="settings-field">
                    <label>Email</label>
                    <input type="text" id="settingsEmail" value="${this.escapeHtml(auth.email || '')}" placeholder="Not signed in" readonly>
                </div>
                <div class="settings-field">
                    <label>User ID</label>
                    <input type="text" id="settingsUserId" value="${this.escapeHtml(auth.user_id || '')}" placeholder="—" readonly>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Plan Management</div>
                <div class="settings-field">
                    <label>Current Plan</label>
                    <input type="text" id="settingsPlan" value="${this.escapeHtml(auth.plan || 'Free')}" readonly>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Billing</div>
                <button class="btn-secondary" id="settingsManageSubscription">Manage Subscription</button>
            </div>
        `;
    }

    renderLLMProvidersTab() {
        const configured = this.settingsState?.configured_llm_providers || [];
        const providers = this.settingsState?.llm_providers || {};
        const selected = this.settingsState?.selected_llm_provider || (configured[0] || SUPPORTED_LLM_PROVIDERS[0]);
        const navItems = SUPPORTED_LLM_PROVIDERS.map(p => {
            const isPopulated = configured.includes(p);
            const isActive = p === selected;
            const label = (LLM_TEMPLATES[p] || {}).label || p.charAt(0).toUpperCase() + p.slice(1);
            return `<button type="button" class="settings-nav-item ${isActive ? 'active' : ''}" data-provider="${p}">
                <span class="status-dot ${isPopulated ? 'populated' : 'empty'}">${isPopulated ? '●' : '○'}</span>
                <span>${this.escapeHtml(label)}</span>
            </button>`;
        }).join('');
        const formHtml = this.renderLLMProviderForm(selected, providers[selected] || {});
        return `
            <div class="settings-split">
                <nav class="settings-nav">
                    <div class="settings-group-title" style="padding:0 16px 8px;margin-bottom:0">Provider</div>
                    ${navItems}
                </nav>
                <div class="settings-main" id="settingsLLMMain">
                    ${formHtml}
                </div>
            </div>
        `;
    }

    renderLLMProviderForm(provider, values = {}) {
        const t = LLM_TEMPLATES[provider] || LLM_TEMPLATES.gemini;
        const label = t.label || provider.charAt(0).toUpperCase() + provider.slice(1);
        let connectivityHtml = '';
        if (t.endpointDefault !== undefined) {
            connectivityHtml += `<div class="settings-field"><label>Endpoint URL</label><input type="url" id="settingsLLMEndpoint" value="${this.escapeHtml(values.endpoint || t.endpointDefault)}"></div>`;
        }
        if (t.needsApiKey) {
            connectivityHtml += `<div class="settings-field"><label>API Key</label><input type="password" id="settingsLLMApiKey" value="${this.escapeHtml(values.api_key || '')}" placeholder="••••••••"></div>`;
        }
        if (t.fields.includes('deployment')) {
            connectivityHtml += `<div class="settings-field"><label>Deployment</label><input type="text" id="settingsLLMDeployment" value="${this.escapeHtml(values.deployment || '')}" placeholder="deployment name"></div>`;
        }
        const modelSection = t.fields.includes('model') ? `
            <div class="settings-group">
                <div class="settings-group-title">Model & Behavior</div>
                <div class="settings-field">
                    <label>Default Model</label>
                    <div class="model-fetch-container">
                        <input type="text" id="settingsLLMModel" list="settingsLLMModelList" value="${this.escapeHtml(values.model || '')}" placeholder="e.g. gpt-4o, gemini-2.5-pro">
                        <datalist id="settingsLLMModelList"></datalist>
                        <button type="button" class="btn-secondary btn-fetch-models" id="settingsLLMGetModels" title="Fetch available models from provider">Get Models</button>
                    </div>
                </div>
            </div>
        ` : '';
        return `
            <input type="hidden" id="settingsLLMProvider" value="${this.escapeHtml(provider)}">
            <div class="settings-group">
                <div class="settings-group-title">Connectivity</div>
                ${connectivityHtml}
            </div>
            ${modelSection}
            <button type="button" class="btn-secondary" id="settingsLLMTest">Test Connection</button>
        `;
    }

    renderDatabaseConnectionsTab() {
        const db = this.settingsState?.databases || { type: 'mysql', hostType: 'local' };
        const dbOptions = ['mysql', 'postgres', 'oracle', 'sqlserver'];
        const dbHtml = this.renderDBTemplate(db.type || 'mysql', db.hostType || 'local', db.cloudProvider || 'aws', db);
        return `
            <div class="settings-group">
                <div class="settings-group-title">Database Type</div>
                <div class="settings-field">
                    <label>Type</label>
                    <select id="settingsDBType">
                        ${dbOptions.map(d => `<option value="${d}" ${(db.type || 'mysql') === d ? 'selected' : ''}>${d.charAt(0).toUpperCase() + d.slice(1)}</option>`).join('')}
                    </select>
                </div>
                <div class="settings-field">
                    <label>Host Type</label>
                    <select id="settingsHostType">
                        <option value="local" ${(db.hostType || 'local') === 'local' ? 'selected' : ''}>Local</option>
                        <option value="cloud" ${(db.hostType || 'local') === 'cloud' ? 'selected' : ''}>Cloud</option>
                    </select>
                </div>
                <div id="settingsDBCloudProvider" style="${(db.hostType || 'local') === 'cloud' ? '' : 'display:none'}">
                    <div class="settings-field">
                        <label>Cloud Provider</label>
                        <select id="settingsCloudProvider">
                            <option value="aws" ${(db.cloudProvider || 'aws') === 'aws' ? 'selected' : ''}>AWS</option>
                            <option value="azure" ${(db.cloudProvider || 'aws') === 'azure' ? 'selected' : ''}>Azure</option>
                        </select>
                    </div>
                </div>
                <div id="settingsDBDynamicFields">${dbHtml}</div>
            </div>
        `;
    }

    renderLLMTemplate(provider, values = {}) {
        const t = LLM_TEMPLATES[provider] || LLM_TEMPLATES.gemini;
        let html = '';
        if (t.endpointDefault !== undefined) {
            html += `<div class="settings-field"><label>Endpoint URL</label><input type="url" id="settingsLLMEndpoint" value="${this.escapeHtml(values.endpoint || t.endpointDefault)}"></div>`;
        }
        if (t.needsApiKey) {
            html += `<div class="settings-field"><label>API Key</label><input type="password" id="settingsLLMApiKey" value="${this.escapeHtml(values.api_key || '')}" placeholder="••••••••"></div>`;
            html += `<div class="settings-field"><label>Model Version</label><input type="text" id="settingsLLMModel" value="${this.escapeHtml(values.model || '')}" placeholder="e.g. gpt-4o"></div>`;
        }
        return html || '';
    }

    renderDBTemplate(dbType, hostType, cloudProvider, values = {}) {
        const t = DB_TEMPLATES[dbType] || DB_TEMPLATES.mysql;
        const port = values.port || t.defaultPort || 3306;
        let html = '';
        if (hostType === 'cloud') {
            const cloud = CLOUD_PROVIDERS[cloudProvider] || CLOUD_PROVIDERS.aws;
            cloud.fields.forEach(f => {
                const val = values[f] || '';
                html += `<div class="settings-field"><label>${f.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}</label><input type="text" id="settingsDB${f}" value="${this.escapeHtml(val)}"></div>`;
            });
        } else {
            t.fields.forEach(f => {
                const val = values[f] || (f === 'port' ? port : '');
                const inputType = f === 'password' ? 'password' : 'text';
                html += `<div class="settings-field"><label>${f.charAt(0).toUpperCase() + f.slice(1).replace(/_/g, ' ')}</label><input type="${inputType}" id="settingsDB${f}" value="${this.escapeHtml(val)}" ${f === 'port' ? `placeholder="${port}"` : ''}></div>`;
            });
        }
        return html;
    }

    renderDataPrivacyTab() {
        const data = this.settingsState?.data_privacy || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">History Control</div>
                <div class="settings-toggle">
                    <label>Save Session History</label>
                    <input type="checkbox" id="settingsSaveHistory" ${(data.save_history !== false) ? 'checked' : ''}>
                </div>
                <button class="btn-danger" id="settingsClearConversations">Clear All Conversations</button>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">RAG & Caching</div>
                <div class="settings-toggle">
                    <label>Semantic Query Caching</label>
                    <input type="checkbox" id="settingsQueryCaching" ${(data.query_caching === true) ? 'checked' : ''}>
                </div>
                <button class="btn-secondary" id="settingsIndexRefresh" style="margin-top:12px">Index Refresh</button>
            </div>
        `;
    }

    renderPreferencesTab() {
        const prefs = this.settingsState?.preferences || {};
        return `
            <div class="settings-group">
                <div class="settings-group-title">Appearance</div>
                <div class="settings-field">
                    <label>Theme</label>
                    <select id="settingsTheme">
                        <option value="light" ${(prefs.theme || 'system') === 'light' ? 'selected' : ''}>Light</option>
                        <option value="dark" ${(prefs.theme || 'system') === 'dark' ? 'selected' : ''}>Dark</option>
                        <option value="system" ${(prefs.theme || 'system') === 'system' ? 'selected' : ''}>System</option>
                    </select>
                </div>
            </div>
            <div class="settings-group">
                <div class="settings-group-title">Output Default</div>
                <div class="settings-toggle">
                    <label>Auto-generate Charts</label>
                    <input type="checkbox" id="settingsAutoCharts" ${(prefs.auto_charts === true) ? 'checked' : ''}>
                </div>
                <div class="settings-field" style="margin-top:8px">
                    <span class="hint">When unchecked, output is raw data only</span>
                </div>
            </div>
        `;
    }

    readLLMFormValues() {
        const getVal = id => document.getElementById(id)?.value ?? '';
        return {
            endpoint: getVal('settingsLLMEndpoint'),
            api_key: getVal('settingsLLMApiKey'),
            model: getVal('settingsLLMModel'),
            deployment: getVal('settingsLLMDeployment')
        };
    }

    bindSettingsDynamicHandlers() {
        const testBtn = document.getElementById('settingsLLMTest');
        if (testBtn) {
            testBtn.onclick = () => this.testLLMConnection();
        }
        const getModelsBtn = document.getElementById('settingsLLMGetModels');
        if (getModelsBtn) {
            getModelsBtn.onclick = () => this.fetchAvailableModels();
        }
        const dbTypeSelect = document.getElementById('settingsDBType');
        const hostTypeSelect = document.getElementById('settingsDBHostType') || document.getElementById('settingsHostType');
        const cloudProviderSelect = document.getElementById('settingsCloudProvider');
        const cloudDiv = document.getElementById('settingsDBCloudProvider');
        const dbFieldsDiv = document.getElementById('settingsDBDynamicFields');
        if (hostTypeSelect) {
            hostTypeSelect.onchange = () => {
                const isCloud = hostTypeSelect.value === 'cloud';
                if (cloudDiv) cloudDiv.style.display = isCloud ? '' : 'none';
                if (dbFieldsDiv) dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect?.value || 'mysql', hostTypeSelect.value, cloudProviderSelect?.value || 'aws', {});
            };
        }
        if (dbTypeSelect && dbFieldsDiv) {
            dbTypeSelect.onchange = () => {
                dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect.value, hostTypeSelect?.value || 'local', cloudProviderSelect?.value || 'aws', {});
            };
        }
        if (cloudProviderSelect && dbFieldsDiv) {
            cloudProviderSelect.onchange = () => {
                dbFieldsDiv.innerHTML = this.renderDBTemplate(dbTypeSelect?.value || 'mysql', 'cloud', cloudProviderSelect.value, {});
            };
        }
        const clearBtn = document.getElementById('settingsClearConversations');
        if (clearBtn) {
            clearBtn.onclick = () => this.clearAllConversations();
        }
        const indexBtn = document.getElementById('settingsIndexRefresh');
        if (indexBtn) {
            indexBtn.onclick = () => this.refreshIndex();
        }
        const manageBtn = document.getElementById('settingsManageSubscription');
        if (manageBtn) {
            manageBtn.onclick = () => this.openStripePortal();
        }
    }

    async clearAllConversations() {
        if (!confirm('Clear all conversations? This cannot be undone.')) return;
        try {
            const response = await fetch('/api/conversations');
            const conversations = await response.json();
            for (const c of conversations) {
                await fetch(`/api/conversations/${c.id}`, { method: 'DELETE' });
            }
            this.startNewChat();
            await this.loadConversations();
            this.closeSettings();
        } catch (e) {
            console.error('Failed to clear conversations:', e);
        }
    }

    async refreshIndex() {
        try {
            await fetch('/api/schema'); // Triggers schema load; backend could add /api/refresh-index
            alert('Index refresh requested.');
        } catch (e) {
            console.error('Failed to refresh index:', e);
        }
    }

    openStripePortal() {
        // TODO: Fetch Stripe portal URL from backend when implemented
        window.open('#', '_blank');
    }

    async testLLMConnection() {
        const provider = this.settingsState?.selected_llm_provider || document.getElementById('settingsLLMProvider')?.value;
        const values = this.readLLMFormValues();
        try {
            const res = await fetch('/api/test-llm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, ...values })
            });
            if (res.ok) {
                const data = await res.json();
                alert(data.success ? 'Connection successful' : (data.error || 'Connection failed'));
            } else {
                alert('Connection test failed');
            }
        } catch (e) {
            alert('Connection test failed: ' + (e.message || 'Network error'));
        }
    }

    async fetchAvailableModels() {
        const btn = document.getElementById('settingsLLMGetModels');
        const provider = this.settingsState?.selected_llm_provider || document.getElementById('settingsLLMProvider')?.value;
        const values = this.readLLMFormValues();
        let apiKey = values.api_key || '';
        // Don't send masked placeholder - backend will use config/env
        const MASKED = ['••••••', '••••••••', '********'];
        if (MASKED.includes(apiKey) || /^[•\u2022*]+$/.test(apiKey)) {
            apiKey = null;
        }
        const baseUrl = values.endpoint || '';

        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Fetching...';
        }
        try {
            const res = await fetch('/api/config/fetch-models', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider,
                    api_key: apiKey || null,
                    base_url: baseUrl || null
                })
            });
            const data = await res.json();
            const datalist = document.getElementById('settingsLLMModelList');
            if (datalist) {
                datalist.innerHTML = '';
                if (data.models && data.models.length > 0) {
                    data.models.forEach(m => {
                        const opt = document.createElement('option');
                        opt.value = m;
                        datalist.appendChild(opt);
                    });
                }
            }
            if (data.error) {
                alert(data.error);
            } else if (data.message && !data.models?.length) {
                alert(data.message);
            }
        } catch (e) {
            alert('Failed to fetch models: ' + (e.message || 'Network error'));
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Get Models';
            }
        }
    }

    buildConfigPayload() {
        const getVal = id => document.getElementById(id)?.value ?? '';
        const account = {
            email: getVal('settingsEmail'),
            user_id: getVal('settingsUserId'),
            plan: getVal('settingsPlan') || 'Free'
        };
        const selected = this.settingsState?.selected_llm_provider || 'gemini';
        if (selected) {
            this.settingsState.llm_providers = this.settingsState.llm_providers || {};
            this.settingsState.llm_providers[selected] = this.readLLMFormValues();
        }
        const llm = { provider: selected };
        const llm_providers = this.settingsState?.llm_providers || {};
        const dbType = getVal('settingsDBType') || 'mysql';
        const hostType = getVal('settingsHostType') || 'local';
        const databases = {
            type: dbType,
            hostType: hostType,
            cloudProvider: getVal('settingsCloudProvider') || 'aws',
            host: getVal('settingsDBhost'),
            port: getVal('settingsDBport'),
            user: getVal('settingsDBuser'),
            password: getVal('settingsDBpassword'),
            database: getVal('settingsDBdatabase'),
            service_name: getVal('settingsDBservice_name'),
            region: getVal('settingsDBregion'),
            secret_arn: getVal('settingsDBsecret_arn'),
            instance: getVal('settingsDBinstance')
        };
        const data_privacy = {
            save_history: document.getElementById('settingsSaveHistory')?.checked ?? true,
            query_caching: document.getElementById('settingsQueryCaching')?.checked ?? false
        };
        const preferences = {
            theme: getVal('settingsTheme') || 'system',
            auto_charts: document.getElementById('settingsAutoCharts')?.checked ?? false
        };
        return { account, llm, llm_providers, databases, data_privacy, preferences };
    }

    async saveSettings() {
        const payload = this.buildConfigPayload();
        try {
            const response = await fetch('/api/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (response.ok) {
                this.settingsState = {
                    ...payload,
                    configured_llm_providers: Object.keys(payload.llm_providers || {}).filter(p => {
                        const v = payload.llm_providers[p];
                        return v && (v.api_key || v.endpoint || v.model);
                    }),
                    selected_llm_provider: payload.llm?.provider || this.settingsState?.selected_llm_provider
                };
                this.closeSettings();
                await this.loadSettings();
            } else {
                console.error('Failed to save settings');
            }
        } catch (e) {
            console.error('Failed to save settings:', e);
        }
    }
    
    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
    
    formatTime(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    window.app = new DaiBaiApp();
});
