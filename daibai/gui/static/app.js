/**
 * DaiBai GUI - Main Application JavaScript
 */

class DaiBaiApp {
    constructor() {
        this.conversationId = null;
        this.ws = null;
        this.isLoading = false;
        
        this.init();
    }
    
    async init() {
        this.bindElements();
        this.bindEvents();
        await this.loadSettings();
        await this.loadConversations();
        this.connectWebSocket();
    }
    
    bindElements() {
        // Navigation
        this.sidebarToggle = document.getElementById('sidebarToggle');
        this.sidebar = document.getElementById('sidebar');
        this.databaseSelect = document.getElementById('databaseSelect');
        this.llmSelect = document.getElementById('llmSelect');
        this.modeSelect = document.getElementById('modeSelect');
        this.schemaBtn = document.getElementById('schemaBtn');
        this.schemaModal = document.getElementById('schemaModal');
        this.schemaModalClose = document.getElementById('schemaModalClose');
        this.schemaContent = document.getElementById('schemaContent');
        
        // Sidebar
        this.newChatBtn = document.getElementById('newChatBtn');
        this.conversationList = document.getElementById('conversationList');
        
        // Chat
        this.messagesContainer = document.getElementById('messagesContainer');
        this.welcomeMessage = document.getElementById('welcomeMessage');
        this.promptInput = document.getElementById('promptInput');
        this.sendBtn = document.getElementById('sendBtn');
        this.executeCheckbox = document.getElementById('executeCheckbox');
    }
    
    bindEvents() {
        // Sidebar toggle
        this.sidebarToggle.addEventListener('click', () => {
            this.sidebar.classList.toggle('collapsed');
        });
        
        // Settings changes
        this.databaseSelect.addEventListener('change', () => this.updateSettings());
        this.llmSelect.addEventListener('change', () => this.updateSettings());
        
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
            
            // Populate database dropdown
            this.databaseSelect.innerHTML = settings.databases
                .map(db => `<option value="${db}" ${db === settings.current_database ? 'selected' : ''}>${db}</option>`)
                .join('');
            
            // Populate LLM dropdown
            this.llmSelect.innerHTML = settings.llm_providers
                .map(llm => `<option value="${llm}" ${llm === settings.current_llm ? 'selected' : ''}>${llm}</option>`)
                .join('');
            
            // Set mode
            this.modeSelect.value = settings.current_mode || 'sql';
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
            this.welcomeMessage.style.display = 'none';
            
            // Clear and render messages
            this.messagesContainer.innerHTML = '';
            conversation.messages.forEach(msg => {
                this.renderMessage(msg);
            });
            
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
        this.messagesContainer.innerHTML = '';
        this.messagesContainer.appendChild(this.welcomeMessage);
        this.welcomeMessage.style.display = 'flex';
        
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
                this.renderAssistantMessage(data.content);
                break;
                
            case 'results':
                this.appendResultsToLastMessage(data);
                break;
                
            case 'error':
                this.removeLoadingIndicator();
                this.renderErrorMessage(data.content);
                break;
                
            case 'done':
                this.isLoading = false;
                this.updateSendButton();
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
        this.renderMessage({
            role: 'user',
            content: query,
            timestamp: new Date().toISOString()
        });
        
        // Clear input
        this.promptInput.value = '';
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
            
            this.renderAssistantMessage(data.sql, data.results);
            
            await this.loadConversations();
        } catch (error) {
            this.removeLoadingIndicator();
            this.renderErrorMessage(error.message);
        } finally {
            this.isLoading = false;
            this.updateSendButton();
        }
    }
    
    renderMessage(msg) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${msg.role}`;
        
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
        this.scrollToBottom();
    }
    
    renderAssistantMessage(sql, results = null) {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        messageEl.id = 'lastAssistantMessage';
        
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
        
        // Bind copy and run buttons
        this.bindSqlActions(messageEl, sql);
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
        
        return `
            <div class="results-container">
                <div class="results-header">
                    <span>${results.length} row(s) returned</span>
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
            this.scrollToBottom();
        }
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
        
        messageEl.innerHTML = `
            <div class="message-avatar" style="background: var(--error);">!</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-role">Error</span>
                </div>
                <div class="message-text" style="color: var(--error);">${this.escapeHtml(error)}</div>
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
