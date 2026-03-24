/**
 * Resume Screener - Frontend Application
 * Handles UI state management and WebSocket communication
 */

// App State
const state = {
    currentView: 'waiting', // waiting, processing, conclusion
    connected: false,
    processingResume: null,
    currentEvaluation: null,
    ws: null,
    reconnectAttempts: 0,
    processingStep: 0,
    stepInterval: null,
};

// DOM Elements
const elements = {
    // Views
    waitingView: document.getElementById('waitingView'),
    processingView: document.getElementById('processingView'),
    conclusionView: document.getElementById('conclusionView'),
    
    // Connection status
    connectionStatus: document.getElementById('connectionStatus'),
    
    // Processing view elements
    processingFileName: document.getElementById('processingFileName'),
    processingFileSize: document.getElementById('processingFileSize'),
    previewContent: document.getElementById('previewContent'),
    previewPlaceholder: document.getElementById('previewPlaceholder'),
    
    // Conclusion view elements
    verdictCard: document.getElementById('verdictCard'),
    verdictBadge: document.getElementById('verdictBadge'),
    verdictIcon: document.getElementById('verdictIcon'),
    verdictLabel: document.getElementById('verdictLabel'),
    finalMessage: document.getElementById('finalMessage'),
    matchedPosition: document.getElementById('matchedPosition'),
    candidateNameTitle: document.getElementById('candidateNameTitle'),
    conclusionDetails: document.getElementById('conclusionDetails'),
    candidateName: document.getElementById('candidateName'),
    positionTitle: document.getElementById('positionTitle'),
    confidenceLevel: document.getElementById('confidenceLevel'),
    summaryText: document.getElementById('summaryText'),
    strengthsList: document.getElementById('strengthsList'),
    gapsList: document.getElementById('gapsList'),
    
    // Buttons
    resetBtn: document.getElementById('resetBtn'),
    viewDetailsBtn: document.getElementById('viewDetailsBtn'),
    
    // Modal elements
    fullReportModal: document.getElementById('fullReportModal'),
    modalContent: document.getElementById('modalContent'),
    closeModalBtn: document.getElementById('closeModalBtn'),
    closeModalFooterBtn: document.getElementById('closeModalFooterBtn'),
    printReportBtn: document.getElementById('printReportBtn'),
    
    // Toast container
    toastContainer: document.getElementById('toastContainer'),
};

// Utility Functions
const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 字节';
    const k = 1024;
    const sizes = ['字节', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

const showToast = (message, type = 'info', duration = 3000) => {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    elements.toastContainer.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
};

// Configuration handling
const updatePollDisplay = () => {
    const sec = state.pollInterval || 5;
    const textEl = document.getElementById('pulseText');
    if (textEl) textEl.textContent = `每${sec}秒监测文件夹`;
    const input = document.getElementById('pollInput');
    if (input) input.value = sec;
};

const fetchConfig = async () => {
    try {
        const resp = await fetch('/api/config');
        if (resp.ok) {
            const data = await resp.json();
            if (data.poll_interval) {
                state.pollInterval = data.poll_interval;
                updatePollDisplay();
            }
        }
    } catch (e) {
        console.error('Failed to fetch config:', e);
    }
};

const setPollInterval = async (secs) => {
    if (secs < 3) {
        showToast('最小监测间隔为3秒', 'error');
        updatePollDisplay();
        return;
    }
    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ poll_interval: secs }),
        });
        if (resp.ok) {
            state.pollInterval = secs;
            updatePollDisplay();
            showToast(`监测间隔已设置为 ${secs} 秒`, 'success');
        } else {
            showToast('更新配置失败', 'error');
        }
    } catch (e) {
        console.error('Error updating poll interval:', e);
        showToast('更新配置失败', 'error');
    }
};

// Render Markdown to sanitized HTML
const renderMarkdown = (md) => {
    if (!md) return '';
    try {
        const html = typeof marked !== 'undefined' ? marked.parse(md) : md;
        return typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
    } catch (e) {
        return md;
    }
};

// View Management
const showView = (viewName) => {
    // Hide all views
    elements.waitingView.classList.add('hidden');
    elements.processingView.classList.add('hidden');
    elements.conclusionView.classList.add('hidden');
    
    // Optionally attach footer to bottom when processing to avoid scroll
    const footerEl = document.querySelector('.footer');
    if (footerEl) {
        if (viewName === 'processing') {
            footerEl.classList.add('fixed-bottom');
            // Make attribution low-key and non-interactive during processing
            footerEl.style.pointerEvents = 'none';
        } else {
            footerEl.classList.remove('fixed-bottom');
            footerEl.style.pointerEvents = '';
        }
    }

    // Show target view
    const viewMap = {
        waiting: elements.waitingView,
        processing: elements.processingView,
        conclusion: elements.conclusionView,
    };

    if (viewMap[viewName]) {
        viewMap[viewName].classList.remove('hidden');
        state.currentView = viewName;
    }
};

// Processing Animation
const startProcessingAnimation = () => {
    state.processingStep = 1;
    updateProcessingStep();
    
    state.stepInterval = setInterval(() => {
        state.processingStep++;
        if (state.processingStep > 4) {
            state.processingStep = 4;
        }
        updateProcessingStep();
    }, 2000);
};

const stopProcessingAnimation = () => {
    if (state.stepInterval) {
        clearInterval(state.stepInterval);
        state.stepInterval = null;
    }
};

const updateProcessingStep = () => {
    const steps = document.querySelectorAll('.step');
    steps.forEach((step, index) => {
        const stepNum = index + 1;
        step.classList.remove('active', 'completed');
        
        if (stepNum === state.processingStep) {
            step.classList.add('active');
        } else if (stepNum < state.processingStep) {
            step.classList.add('completed');
        }
    });
};

// WebSocket Connection
const connectWebSocket = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    state.ws = new WebSocket(wsUrl);
    
    state.ws.onopen = () => {
        console.log('WebSocket connected');
        state.connected = true;
        state.reconnectAttempts = 0;
        updateConnectionStatus();
        
        // Check current status
        fetchCurrentStatus();
    };
    
    state.ws.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            handleWebSocketMessage(message);
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };
    
    state.ws.onclose = () => {
        console.log('WebSocket disconnected');
        state.connected = false;
        updateConnectionStatus();
        
        // Reconnect with exponential backoff
        const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 30000);
        state.reconnectAttempts++;
        
        setTimeout(() => {
            console.log(`Reconnecting... (attempt ${state.reconnectAttempts})`);
            connectWebSocket();
        }, delay);
    };
    
    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
};

const updateConnectionStatus = () => {
    const statusEl = elements.connectionStatus;
    const dot = statusEl.querySelector('.status-dot');
    const text = statusEl.querySelector('.status-text');
    
    if (state.connected) {
        statusEl.classList.add('connected');
        statusEl.classList.remove('error');
        text.textContent = '已连接';
    } else {
        statusEl.classList.remove('connected');
        statusEl.classList.add('error');
        text.textContent = '正在重连...';
    }
};

// Event Handlers
const updatePreviewForResume = (resume) => {
    console.log('updatePreviewForResume called with:', resume);

    if (!resume) {
        elements.previewContent.innerHTML = '';
        elements.previewContent.classList.add('hidden');
        elements.previewPlaceholder.classList.remove('hidden');
        elements.processingFileName.textContent = '未知文件';
        elements.processingFileSize.textContent = formatFileSize(0);
        return;
    }

    elements.processingFileName.textContent = resume.original_name || '未知文件';
    elements.processingFileSize.textContent = formatFileSize(resume.file_size || 0);

    const ext = (resume.original_name || '').toLowerCase().split('.').pop();
    const previewUrl = `/api/preview/${resume.id}`;

    console.log('Detected extension for preview:', ext, 'previewUrl:', previewUrl);

    // Only show live preview for image resumes; PDFs and other formats
    // will use the placeholder card with filename + size.
    if (['png', 'jpg', 'jpeg', 'webp'].includes(ext)) {
        elements.previewContent.innerHTML = `<img src="${previewUrl}" alt="Resume Preview">`;
        elements.previewContent.classList.remove('hidden');
        elements.previewPlaceholder.classList.add('hidden');
    } else {
        elements.previewContent.innerHTML = '';
        elements.previewContent.classList.add('hidden');
        elements.previewPlaceholder.classList.remove('hidden');
    }
};

const handleWebSocketMessage = (message) => {
    const { type, data } = message;
    
    console.log(`[${new Date().toISOString()}] WebSocket message: ${type}`, data);
    
    // Ignore processing events if we've already seen completed for this resume
    if (type === 'processing' && state.currentEvaluation) {
        console.log('Ignoring processing event - already have evaluation');
        return;
    }
    
    switch (type) {
        case 'detected':
            // New file detected
            showToast(`检测到新简历：${data.original_name}`, 'info');
            break;
            
        case 'processing':
            // Start processing view
            state.processingResume = data;
            updatePreviewForResume(state.processingResume);
            
            showView('processing');
            startProcessingAnimation();
            break;
            
        case 'completed':
            // Show conclusion
            stopProcessingAnimation();
            console.log('Received completion data:', JSON.stringify(data, null, 2));
            state.currentEvaluation = data;
            renderConclusion(data);
            showView('conclusion');
            break;
            
        case 'error':
            stopProcessingAnimation();
            showToast(`错误：${data.error || '处理失败'}`, 'error');
            showView('waiting');
            break;
            
        case 'connected':
            console.log(data.message);
            break;
            
        case 'keepalive':
        case 'pong':
            // Ignore keepalive messages
            break;
            
        default:
            console.log('Unknown message type:', type);
    }
};

// Render Conclusion
const renderConclusion = (data) => {
    // Safely get values with defaults
    const verdictColor = data.verdict_color || 'neutral';
    const verdictDisplay = data.verdict_display || 'Pending Review';
    const candidateName = data.candidate_name || '—';
    const positionTitle = data.position_title || '—';
    const confidence = (data.confidence || 'medium').toLowerCase();
    const summary = data.summary || 'No summary available.';
    const strengths = data.strengths || [];
    const gaps = data.gaps || [];
    
    // Update verdict card styling
    elements.verdictCard.className = `verdict-card ${verdictColor}`;
    
    // Update badge and text
    const iconMap = {
        success: '✓',
        warning: '◷',
        danger: '✕',
        neutral: '?',
    };
    elements.verdictIcon.textContent = iconMap[verdictColor] || '?';
    // verdictText element removed; final message above will display more expressive text

    // set candidate name title
    if (elements.candidateNameTitle) {
        elements.candidateNameTitle.textContent = candidateName;
    }
    // compute final message based on verdict/confidence
    let finalMsg = '';
    if (data.verdict === 'invite') {
        if (confidence === 'high') {
            finalMsg = '哇塞！到我们后台聊聊';
        } else {
            finalMsg = '你背景很相关！我们约个时间聊聊';
        }
    } else {
        finalMsg = '哎呀，好像没有适配的岗位呢';
    }
    elements.finalMessage.textContent = finalMsg;
    // show matched position under message if available
    elements.matchedPosition.textContent = positionTitle && positionTitle !== '—' ? positionTitle : '';

    // hide original detail container since details are now in modal
    if (elements.conclusionDetails) {
        elements.conclusionDetails.style.display = 'none';
    }

    // Update candidate info (still populated for modal)
    elements.candidateName.textContent = candidateName;
    elements.positionTitle.textContent = positionTitle;
    const confMap = { low: '低', medium: '中', high: '高' };
    elements.confidenceLevel.textContent = confMap[confidence] || confidence;
    
    // Update summary with better Chinese fallback
    elements.summaryText.innerHTML = summary ? renderMarkdown(summary) : '（暂无详细评估摘要）';
    
    // Update strengths
    elements.strengthsList.innerHTML = '';
    elements.strengthsList.classList.remove('empty');
    if (strengths.length > 0) {
        strengths.forEach(strength => {
            if (strength) {  // Only add non-empty strings
                const li = document.createElement('li');
                li.innerHTML = renderMarkdown(strength);
                elements.strengthsList.appendChild(li);
            }
        });
    }
    if (elements.strengthsList.children.length === 0) {
        elements.strengthsList.classList.add('empty');
    }
    
    // Update gaps
    elements.gapsList.innerHTML = '';
    elements.gapsList.classList.remove('empty');
    if (gaps.length > 0) {
        gaps.forEach(gap => {
            if (gap) {  // Only add non-empty strings
                const li = document.createElement('li');
                li.innerHTML = renderMarkdown(gap);
                elements.gapsList.appendChild(li);
            }
        });
    }
    if (elements.gapsList.children.length === 0) {
        elements.gapsList.classList.add('empty');
    }
};

// API Calls
const fetchCurrentStatus = async () => {
    try {
        const response = await fetch('/api/current');
        const data = await response.json();
        
        if (data.status === 'processing' && data.resume) {
            // Resume is being processed
            state.processingResume = data.resume;
            updatePreviewForResume(state.processingResume);
            showView('processing');
            startProcessingAnimation();
        }
    } catch (e) {
        console.error('Failed to fetch current status:', e);
    }
};

const fetchEvaluation = async (evaluationId) => {
    try {
        const response = await fetch(`/api/evaluation/${evaluationId}`);
        if (response.ok) {
            const data = await response.json();
            state.currentEvaluation = data;
            renderConclusion(data);
            showView('conclusion');
        }
    } catch (e) {
        console.error('Failed to fetch evaluation:', e);
    }
};

// Modal Functions
const openModal = () => {
    elements.fullReportModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    renderFullReport();
};

const closeModal = () => {
    elements.fullReportModal.classList.add('hidden');
    document.body.style.overflow = '';
};

const renderFullReport = () => {
    if (!state.currentEvaluation) return;
    
    const data = state.currentEvaluation;
    const resume = state.processingResume || {};
    
    // Format date
    const evaluatedAt = data.evaluated_at 
        ? new Date(data.evaluated_at).toLocaleString()
        : '未知';
    
    // Format file size
    const fileSize = resume.file_size ? formatFileSize(resume.file_size) : '未知';
    
    // Verdict styling
    const verdictClass = `verdict-${data.verdict || 'waitlist'}`;
    const verdictText = data.verdict_display || 'Pending Review';
    
    // Prepare strengths and gaps HTML with Markdown rendering
    const strengthsArr = data.strengths || [];
    const gapsArr = data.gaps || [];
    const strengthsHtml = strengthsArr.length > 0
        ? `<ul class="report-list">${strengthsArr.map(s => `<li>${renderMarkdown(s)}</li>`).join('')}</ul>`
        : '<div class="report-text-block">未检测到具体优势。</div>';
    const gapsHtml = gapsArr.length > 0
        ? `<ul class="report-list">${gapsArr.map(g => `<li>${renderMarkdown(g)}</li>`).join('')}</ul>`
        : '<div class="report-text-block">未检测到具体不足。</div>';

    const summaryHtml = data.summary ? renderMarkdown(data.summary) : '<div>暂无摘要。</div>';
    const reasoningHtml = data.reasoning ? renderMarkdown(data.reasoning) : '<div>暂无详细推理。</div>';

    const html = `
        <!-- 候选人概览 -->
        <div class="report-section">
            <div class="report-section-title">候选人概览</div>
            <div class="report-grid">
                <div class="report-field">
                    <span class="report-field-label">候选人姓名</span>
                    <span class="report-field-value">${data.candidate_name || '未提取'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">简历文件</span>
                    <span class="report-field-value">${resume.original_name || '未知'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">职位</span>
                    <span class="report-field-value">${data.position_title || '未知'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">文件大小</span>
                    <span class="report-field-value">${fileSize}</span>
                </div>
            </div>
        </div>

        <!-- 筛选决策 -->
        <div class="report-section">
            <div class="report-section-title">筛选决策</div>
            <div class="report-grid">
                <div class="report-field">
                    <span class="report-field-label">最终结论</span>
                    <span class="report-field-value ${verdictClass}">${verdictText}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">置信度</span>
                    <span class="report-field-value">${(data.confidence || '中')}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">工作年限（年）</span>
                    <span class="report-field-value">${data.experience_years !== null ? data.experience_years : '未知'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">处理时间</span>
                    <span class="report-field-value">${data.processing_time_seconds ? Math.round(data.processing_time_seconds) + 's' : '暂无'}</span>
                </div>
            </div>
        </div>

        <!-- 评估摘要 -->
        <div class="report-section">
            <div class="report-section-title">评估摘要</div>
            <div class="report-text-block">${summaryHtml}</div>
        </div>

        <!-- 详细分析 -->
        <div class="report-section">
            <div class="report-section-title">详细分析</div>
            <div class="report-grid">
                <div class="report-field full-width">
                    <span class="report-field-label">关键优势 (${strengthsArr.length})</span>
                    ${strengthsHtml}
                </div>
                <div class="report-field full-width">
                    <span class="report-field-label">待探索/不足 (${gapsArr.length})</span>
                    ${gapsHtml}
                </div>
            </div>
        </div>

        <!-- AI 相关 -->
        <div class="report-section">
            <div class="report-section-title">AI协作能力评估</div>
            <div class="report-grid">
                <div class="report-field">
                    <span class="report-field-label">使用AI工具</span>
                    <span class="report-field-value">${data.ai_competency?.uses_ai_tools ? '✓ 检测到' : '✗ 未检测到'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">项目/作品证据</span>
                    <span class="report-field-value">${data.ai_competency?.has_projects ? '✓ 有作品' : '✗ 未明确'}</span>
                </div>
                <div class="report-field">
                    <span class="report-field-label">Ownership意识</span>
                    <span class="report-field-value">${data.ai_competency?.ownership_mindset ? '✓ 体现责任' : '✗ 未明确'}</span>
                </div>
            </div>
        </div>

        <!-- 评估者推理 -->
        <div class="report-section">
            <div class="report-section-title">评估者推理</div>
            <div class="report-text-block">${reasoningHtml}</div>
        </div>

        <!-- 元信息 -->
        <div class="report-meta">
            <span>评估时间: ${evaluatedAt}</span>
            <span>评估ID: ${data.id || '无'}</span>
        </div>
    `;

    elements.modalContent.innerHTML = html;
};

// Event Listeners
elements.resetBtn.addEventListener('click', () => {
    showView('waiting');
    state.currentEvaluation = null;
    state.processingResume = null;
    if (elements.finalMessage) elements.finalMessage.textContent = '';
    if (elements.matchedPosition) elements.matchedPosition.textContent = '';
    if (elements.candidateNameTitle) elements.candidateNameTitle.textContent = '';
});

// poll interval input handler
const pollInputEl = document.getElementById('pollInput');
if (pollInputEl) {
    pollInputEl.addEventListener('change', (e) => {
        const val = parseInt(e.target.value, 10);
        if (!isNaN(val)) {
            setPollInterval(val);
        }
    });
}

elements.viewDetailsBtn.addEventListener('click', () => {
    if (state.currentEvaluation) {
        openModal();
    } else {
        showToast('暂无评估数据', 'error');
    }
});

elements.closeModalBtn.addEventListener('click', closeModal);
elements.closeModalFooterBtn.addEventListener('click', closeModal);

// Close modal on backdrop click
elements.fullReportModal.addEventListener('click', (e) => {
    if (e.target === elements.fullReportModal) {
        closeModal();
    }
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !elements.fullReportModal.classList.contains('hidden')) {
        closeModal();
    }
});

// Print functionality
elements.printReportBtn.addEventListener('click', () => {
    window.print();
});

// Keep WebSocket alive
setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'ping' }));
    }
}, 25000);

// Initialize
const init = () => {
    connectWebSocket();
    fetchConfig();
    console.log('Resume Screener initialized');
};

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
