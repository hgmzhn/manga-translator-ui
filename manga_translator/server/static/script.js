document.addEventListener('DOMContentLoaded', () => {
    init();
});

// ===== 公告功能 =====
async function loadAnnouncement() {
    try {
        const res = await fetch('/announcement');
        const announcement = await res.json();
        
        if (announcement.enabled && announcement.message) {
            const banner = document.getElementById('announcement-banner');
            banner.textContent = announcement.message;
            banner.style.display = 'block';
            
            // 根据类型设置颜色
            const colors = {
                'info': { bg: '#E3F2FD', text: '#1976D2', border: '#2196F3' },
                'warning': { bg: '#FFF3E0', text: '#E65100', border: '#FF9800' },
                'error': { bg: '#FFEBEE', text: '#C62828', border: '#F44336' }
            };
            
            const color = colors[announcement.type] || colors.info;
            banner.style.backgroundColor = color.bg;
            banner.style.color = color.text;
            banner.style.borderBottom = `3px solid ${color.border}`;
        }
    } catch (e) {
        console.error('Failed to load announcement:', e);
    }
}

// State
let configSchema = {};
let fileList = [];
const hiddenKeys = [
    // 通过其他UI控制的参数
    'upscale.realcugan_model',  // 通过 upscale_ratio 动态控制
    'cli.load_text',  // 通过工作流模式下拉框控制
    'cli.translate_json_only',  // 通过工作流模式下拉框控制
    'cli.template',  // 通过工作流模式下拉框控制
    'cli.generate_and_export',  // 通过工作流模式下拉框控制
    'cli.colorize_only',  // 通过工作流模式下拉框控制
    'cli.upscale_only',  // 通过工作流模式下拉框控制
    'cli.inpaint_only',  // 通过工作流模式下拉框控制
    // 翻译后检查参数（默认隐藏）
    'translator.enable_post_translation_check',
    'translator.post_check_max_retry_attempts',
    'translator.post_check_repetition_threshold',
    'translator.post_check_target_lang_threshold',
    // 高级翻译器参数（默认隐藏）
    'translator.translator_chain',  // 链式翻译
    'translator.selective_translation',  // Selective Translation
    'translator.skip_lang',  // Skip Lang
    'use_custom_api_params',  // 仅用于服务器端，用户端不显示
    // 废弃参数
    'render.gimp_font',  // 已废弃，使用 font_family 代替
];

// User session and permissions
let userSession = null;
let userPermissions = null;

// DOM Elements
const els = {
    fileInput: document.getElementById('file-input'),
    folderInput: document.getElementById('folder-input'),
    addFilesBtn: document.getElementById('add-files-btn'),
    addFolderBtn: document.getElementById('add-folder-btn'),
    clearListBtn: document.getElementById('clear-list-btn'),
    fileList: document.getElementById('file-list'),
    fileCount: document.getElementById('file-count'),
    startBtn: document.getElementById('start-btn'),
    workflowMode: document.getElementById('workflow-mode'),
    logBox: document.getElementById('log-box'),
    clearLogBtn: document.getElementById('clear-log-btn'),
    tabs: document.querySelectorAll('.tab-btn'),
    tabPanes: document.querySelectorAll('.tab-pane'),
    exportConfigBtn: document.getElementById('export-config-btn'),
    importConfigBtn: document.getElementById('import-config-btn'),
    configInput: document.getElementById('config-input'),
};

let configOptions = {};  // 存储参数选项

// ===== Authentication and Permission Functions =====

async function checkAuthentication() {
    // Check if user has a session token
    const sessionToken = localStorage.getItem('session_token');
    
    if (!sessionToken) {
        // No session token, redirect to login
        window.location.href = '/static/login.html';
        return false;
    }
    
    try {
        // Verify session with server
        const res = await fetch('/auth/check', {
            headers: {
                'X-Session-Token': sessionToken
            }
        });
        
        if (!res.ok) {
            // Session invalid, redirect to login
            localStorage.removeItem('session_token');
            window.location.href = '/static/login.html';
            return false;
        }
        
        const data = await res.json();
        
        if (!data.valid) {
            // Session invalid, redirect to login
            localStorage.removeItem('session_token');
            window.location.href = '/static/login.html';
            return false;
        }
        
        // Store user session info
        userSession = {
            username: data.user.username,
            role: data.user.role
        };
        
        // Update UI with user info
        updateUserInfo();
        
        return true;
    } catch (e) {
        console.error('Failed to check authentication:', e);
        localStorage.removeItem('session_token');
        window.location.href = '/static/login.html';
        return false;
    }
}

async function loadUserPermissions() {
    const sessionToken = localStorage.getItem('session_token');
    
    if (!sessionToken) {
        return;
    }
    
    try {
        // Load config with authenticated mode to get user permissions
        const res = await fetch('/config?mode=authenticated', {
            headers: {
                'X-Session-Token': sessionToken
            }
        });
        
        if (!res.ok) {
            throw new Error('Failed to load user permissions');
        }
        
        const data = await res.json();
        
        // Extract user permissions from config response
        if (data.user_permissions) {
            userPermissions = data.user_permissions;
            console.log('User permissions loaded:', userPermissions);
            
            // Apply permission-based UI filtering
            applyPermissionFiltering();
            
            // Update workflow select based on allowed_workflows
            if (userPermissions.allowed_workflows) {
                availableWorkflows = userPermissions.allowed_workflows;
                updateWorkflowSelect();
            }
        }
    } catch (e) {
        console.error('Failed to load user permissions:', e);
    }
}

function updateUserInfo() {
    if (!userSession) return;
    
    // Show user info
    const userInfoEl = document.getElementById('user-info');
    const usernameEl = document.getElementById('username-display');
    const logoutBtn = document.getElementById('logout-btn');
    
    if (userInfoEl && usernameEl) {
        usernameEl.textContent = userSession.username;
        userInfoEl.style.display = 'inline';
    }
    
    // Show logout button
    if (logoutBtn) {
        logoutBtn.style.display = 'inline-block';
    }
    
    // Show admin link only for admin users
    const adminLink = document.getElementById('admin-link');
    if (adminLink && userSession.role === 'admin') {
        adminLink.style.display = 'inline-block';
    }
}

function applyPermissionFiltering() {
    if (!userPermissions) return;
    
    console.log('Applying permission filtering...');
    
    // Hide parameters that user doesn't have permission for
    document.querySelectorAll('[data-key]').forEach(el => {
        const fullKey = el.dataset.key;
        
        // Check if user has permission for this parameter
        const hasPermission = userPermissions.allowed_parameters.includes('*') || 
                            userPermissions.allowed_parameters.includes(fullKey);
        
        if (!hasPermission) {
            // Hide the entire form group
            const formGroup = el.closest('.form-group');
            if (formGroup) {
                formGroup.style.display = 'none';
            }
        }
    });
}

async function logout() {
    const sessionToken = localStorage.getItem('session_token');
    
    if (sessionToken) {
        try {
            await fetch('/auth/logout', {
                method: 'POST',
                headers: {
                    'X-Session-Token': sessionToken
                }
            });
        } catch (e) {
            console.error('Logout error:', e);
        }
    }
    
    // Clear session token and redirect to login
    localStorage.removeItem('session_token');
    window.location.href = '/static/login.html';
}

async function init() {
    // Check authentication first
    const isAuthenticated = await checkAuthentication();
    if (!isAuthenticated) {
        return; // Will redirect to login
    }
    
    // 加载公告
    await loadAnnouncement();
    
    setupEventListeners();
    await loadI18n(); // Load i18n first
    await loadUserPermissions(); // Load user permissions
    await loadUserSettings(); // Load user visibility settings
    await loadConfigOptions(); // Load parameter options (先加载选项数据)
    await loadConfig(); // 创建配置表单（会使用 configOptions 填充下拉框）
    // 更新字体和提示词管理列表（不是下拉框，是管理面板中的列表）
    await refreshFontResources();
    if (configOptions['high_quality_prompt_path']) {
        updatePromptList(configOptions['high_quality_prompt_path']);
    }
    await loadTranslators(); // Load translators
    await loadLanguages(); // Load languages
    await loadWorkflows(); // Load workflows
    await loadPresets(); // Load presets
    
    // 在所有数据加载完成后，填充下拉菜单
    populateDropdowns();
    
    // 添加预设选择器到基础设置
    addPresetSelectorToUI();
    
    // 只有在允许用户编辑 API keys 时才从 localStorage 恢复
    // 否则清除旧数据，避免覆盖服务器的 API keys
    try {
        const saved = localStorage.getItem('user_env_vars');
        if (saved) {
            const schema = getApiKeySchema();
            const validKeys = new Set(schema.envKeys || []);
            const parsed = JSON.parse(saved);
            currentEnvVars = Object.fromEntries(
                Object.entries(parsed).filter(([key, value]) => validKeys.has(key) && value)
            );
        }
    } catch (e) {
        console.error('Failed to load API keys from localStorage:', e);
    }
    
    // 加载保存的翻译结果
    loadResults();
    
    // 加载翻译历史
    await loadTranslationHistory();
    
    startLogPolling(); // Start polling logs
    
    // 初始化移动端菜单
    initMobileMenu();
    
    // 初始化移动端图片查看器（双指缩放、双击重置）
    initMobileImageViewer();
}

async function checkUserAccess() {
    try {
        // 首先检查管理员是否已设置密码
        const setupRes = await fetch('/auth/status');
        const setupData = await setupRes.json();
        
        if (setupData.need_setup) {
            // 管理员还没设置密码，提示用户先设置
            const setupPrompt = document.createElement('div');
            setupPrompt.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.8);
                display: flex;
                justify-content: center;
                align-items: center;
                z-index: 10000;
            `;
            setupPrompt.innerHTML = `
                <div style="background: white; padding: 40px; border-radius: 8px; max-width: 500px; text-align: center;">
                    <h2 style="color: #2196F3; margin-bottom: 20px;">⚠️ 首次使用提示</h2>
                    <p style="font-size: 16px; color: #333; margin-bottom: 30px;">
                        检测到管理员密码尚未设置。<br>
                        为了系统安全，请先设置管理员密码。
                    </p>
                    <button onclick="window.location.href='/admin'" 
                            style="background: #2196F3; color: white; border: none; padding: 12px 30px; 
                                   font-size: 16px; border-radius: 4px; cursor: pointer;">
                        前往设置管理员密码
                    </button>
                </div>
            `;
            document.body.appendChild(setupPrompt);
            throw new Error('Admin password not set');
        }
        
        // 管理员密码已设置，检查用户端访问权限
        const res = await fetch('/user/access');
        const data = await res.json();
        
        if (data.require_password) {
            // 检查是否已经登录
            const isLoggedIn = sessionStorage.getItem('user_logged_in');
            if (!isLoggedIn) {
                // 显示登录界面
                document.getElementById('user-login-screen').style.display = 'flex';
                // 阻止页面交互
                return new Promise((resolve) => {
                    window.userLoginResolve = resolve;
                });
            }
        }
    } catch (e) {
        console.error('Failed to check user access:', e);
        // 如果是管理员密码未设置的错误，不继续执行
        if (e.message === 'Admin password not set') {
            throw e;
        }
    }
}

async function userLogin() {
    const password = document.getElementById('user-password-input').value;
    const errorEl = document.getElementById('user-login-error');
    
    try {
        const formData = new FormData();
        formData.append('password', password);
        
        const res = await fetch('/user/login', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        if (data.success) {
            // 登录成功
            sessionStorage.setItem('user_logged_in', 'true');
            document.getElementById('user-login-screen').style.display = 'none';
            errorEl.textContent = '';
            
            // 继续初始化
            if (window.userLoginResolve) {
                window.userLoginResolve();
            }
        } else {
            errorEl.textContent = data.detail || data.message || '密码错误，请重试';
        }
    } catch (e) {
        errorEl.textContent = '登录失败：' + e.message;
    }
}

// 监听回车键登录
document.addEventListener('DOMContentLoaded', () => {
    const passwordInput = document.getElementById('user-password-input');
    if (passwordInput) {
        passwordInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                userLogin();
            }
        });
    }
});

async function loadConfigOptions() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        if (sessionToken) {
            headers['X-Session-Token'] = sessionToken;
        }
        const res = await fetch('/config/options?mode=user', { headers });
        configOptions = await res.json();
        console.log('Loaded config options:', configOptions);
        console.log('high_quality_prompt_path options:', configOptions['high_quality_prompt_path']);
        console.log('font_family options:', configOptions['font_family']);
        console.log('layout_mode options:', configOptions['layout_mode']);
        
        // 字体和提示词选项将在 init 函数中 loadConfig 之后更新
    } catch (e) {
        console.error('Error loading config options:', e);
    }
}

// --- i18n Handling ---
let i18nData = {};
let currentLocale = 'zh_CN'; // Default

async function loadI18n() {
    try {
        // 1. 从 localStorage 读取用户选择的语言
        const savedLocale = localStorage.getItem('locale');
        if (savedLocale) {
            currentLocale = savedLocale;
        } else {
            // 2. 检测浏览器语言
            const browserLang = navigator.language.replace('-', '_');
            if (browserLang.startsWith('en')) currentLocale = 'en_US';
            else if (browserLang.startsWith('zh-CN') || browserLang.startsWith('zh_CN')) currentLocale = 'zh_CN';
            else if (browserLang.startsWith('zh-TW') || browserLang.startsWith('zh_TW')) currentLocale = 'zh_TW';
            else if (browserLang.startsWith('ja')) currentLocale = 'ja_JP';
            else if (browserLang.startsWith('ko')) currentLocale = 'ko_KR';
            else if (browserLang.startsWith('es')) currentLocale = 'es_ES';
        }

        // 3. 从服务器获取翻译数据（使用桌面版的语言文件）
        console.log(`Loading i18n for locale: ${currentLocale}`);
        const res = await fetch(`/i18n/${currentLocale}`);
        i18nData = await res.json();
        console.log(`i18n data loaded, keys count: ${Object.keys(i18nData).length}`);
        console.log('Sample keys:', Object.keys(i18nData).slice(0, 10));

        // 4. 更新语言选择器
        const langSelect = document.getElementById('language-select');
        if (langSelect) {
            langSelect.value = currentLocale;
        }

        applyTranslations();
    } catch (e) {
        console.error("Failed to load i18n", e);
        // 回退到英语
        try {
            const res = await fetch('/i18n/en_US');
            i18nData = await res.json();
            applyTranslations();
        } catch (fallbackError) {
            console.error("Failed to load fallback language", fallbackError);
        }
    }
}

async function changeLanguage(locale) {
    currentLocale = locale;
    localStorage.setItem('locale', locale);
    
    try {
        const res = await fetch(`/i18n/${locale}`);
        i18nData = await res.json();
        applyTranslations();
        
        // 重新生成配置UI以应用新的翻译
        if (configSchema && Object.keys(configSchema).length > 0) {
            // 清空现有配置UI
            document.getElementById('basic-left').innerHTML = '';
            document.getElementById('basic-right').innerHTML = '';
            document.getElementById('advanced-left').innerHTML = '';
            document.getElementById('advanced-right').innerHTML = '';
            document.getElementById('options-left').innerHTML = '';
            document.getElementById('options-right').innerHTML = '';
            
            // 重新生成
            generateConfigUI(configSchema);
        }
        
        const langMsg = currentLocale.startsWith('zh') ? '语言已切换' : 'Language changed';
        log(`${langMsg}: ${locale}`, 'info');
    } catch (e) {
        console.error("Failed to change language", e);
        const errorMsg = currentLocale.startsWith('zh') ? '切换语言失败' : 'Failed to change language';
        log(errorMsg, 'error');
    }
}

function t(key, defaultText = '') {
    // 如果 i18nData 还没加载，返回默认值或键名
    if (!i18nData || Object.keys(i18nData).length === 0) {
        return defaultText || key;
    }
    
    let text = i18nData[key] || defaultText || key;
    // Remove & shortcuts (e.g. "&File" -> "File")
    return text.replace(/&/g, '');
}

function applyTranslations() {
    // Static elements
    document.title = t('Manga Translator', 'Manga Translator Web UI');
    document.querySelector('header h1').textContent = t('Manga Translator');
    
    // Admin link
    const adminLinkText = document.getElementById('admin-link-text');
    if (adminLinkText) {
        adminLinkText.textContent = t('admin', '管理');
    }

    // Left Panel - 使用桌面版已有的键名
    const fileListHeader = document.querySelector('.file-section h3');
    if (fileListHeader) fileListHeader.textContent = '文件列表'; // 暂时硬编码，因为桌面版没有这个键
    
    document.getElementById('add-files-btn').textContent = t('Add Files');
    document.getElementById('clear-list-btn').textContent = t('Clear List');
    
    // 输出目录在 Web 模式下隐藏，不需要翻译
    // const outputHeader = document.querySelector('.output-section h3');
    // if (outputHeader) outputHeader.textContent = t('Output Directory:');
    // document.getElementById('output-dir').placeholder = t('Select or drag output folder...');
    document.querySelector('.workflow-section h3').textContent = t('Translation Workflow Mode:');
    document.getElementById('start-btn').textContent = t('Start Translation');
    document.getElementById('export-config-btn').textContent = t('Export Config');
    document.getElementById('import-config-btn').textContent = t('Import Config');

    // Tabs
    const tabs = document.querySelectorAll('.tab-btn');
    if (tabs[0]) tabs[0].textContent = t('Basic Settings');
    if (tabs[1]) tabs[1].textContent = t('Advanced Settings');
    if (tabs[2]) tabs[2].textContent = t('Options');
    if (tabs[3]) tabs[3].textContent = t('API Keys (.env)');
    
    // API Keys hint
    const envHint = document.getElementById('env-hint-text');
    if (envHint) envHint.textContent = t('env_hint', '根据选择的翻译器，下方会显示所需的 API 密钥输入框');

    // Log Header
    document.querySelector('.log-header h3').textContent = t('Log output...');

    // Workflow Options
    const workflowSelect = document.getElementById('workflow-mode');
    // We need to map values to keys. 
    // Since options are hardcoded in HTML, we can update them by value.
    const workflowMap = {
        'normal': 'Normal Translation',
        'export_trans': 'Export Translation',
        'export_raw': 'Export Original Text',
        'import_trans': 'Import Translation and Render',
        'colorize': 'Colorize Only',
        'upscale': 'Upscale Only',
        'inpaint': 'Inpaint Only' // Check key
    };

    Array.from(workflowSelect.options).forEach(opt => {
        const key = workflowMap[opt.value];
        if (key) opt.textContent = t(key, opt.textContent);
    });
}

function setupEventListeners() {
    // File Management
    els.addFilesBtn.addEventListener('click', () => els.fileInput.click());
    els.addFolderBtn.addEventListener('click', () => els.folderInput.click());
    els.fileInput.addEventListener('change', handleFileSelect);
    els.folderInput.addEventListener('change', handleFolderSelect);
    els.clearListBtn.addEventListener('click', clearFileList);

    // Tabs
    els.tabs.forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Actions
    els.startBtn.addEventListener('click', startTask);
    els.clearLogBtn.addEventListener('click', () => els.logBox.innerHTML = '');

    // Config IO
    els.exportConfigBtn.addEventListener('click', exportConfig);
    els.importConfigBtn.addEventListener('click', () => els.configInput.click());
    els.configInput.addEventListener('change', importConfig);

    // Logout button
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', logout);
    }

    // Language selector
    const langSelect = document.getElementById('language-select');
    if (langSelect) {
        langSelect.addEventListener('change', async (e) => {
            currentLocale = e.target.value;
            localStorage.setItem('locale', currentLocale);
            await loadI18n();
        });
    }
    
    // Upload handlers
    const fontUploadBtn = document.getElementById('font-upload-btn');
    const fontUploadInput = document.getElementById('font-upload-input');
    if (fontUploadBtn && fontUploadInput) {
        fontUploadBtn.addEventListener('click', () => fontUploadInput.click());
        fontUploadInput.addEventListener('change', handleFontUpload);
    }
    
    const promptUploadBtn = document.getElementById('prompt-upload-btn');
    const promptUploadInput = document.getElementById('prompt-upload-input');
    if (promptUploadBtn && promptUploadInput) {
        promptUploadBtn.addEventListener('click', () => promptUploadInput.click());
        promptUploadInput.addEventListener('change', handlePromptUpload);
    }
    
    // Results handlers
    const expandResultsBtn = document.getElementById('expand-results-btn');
    const downloadAllBtn = document.getElementById('download-all-btn');
    const clearResultsBtn = document.getElementById('clear-results-btn');
    if (expandResultsBtn) {
        expandResultsBtn.addEventListener('click', openImageViewer);
    }
    if (downloadAllBtn) {
        downloadAllBtn.addEventListener('click', downloadAllResults);
    }
    if (clearResultsBtn) {
        clearResultsBtn.addEventListener('click', clearResults);
    }
}

// --- Config Handling ---

async function loadConfig() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        
        // Use authenticated mode if we have a session token
        let url = '/config';
        if (sessionToken) {
            url = '/config?mode=authenticated';
            headers['X-Session-Token'] = sessionToken;
        }
        
        const res = await fetch(url, { headers });
        const data = await res.json();
        
        // Extract user permissions if present
        if (data.user_permissions) {
            userPermissions = data.user_permissions;
            console.log('User permissions from config:', userPermissions);
            
            // Update workflow select based on allowed_workflows
            if (userPermissions.allowed_workflows) {
                availableWorkflows = userPermissions.allowed_workflows;
                updateWorkflowSelect();
            }
            
            // Remove user_permissions from config before generating UI
            delete data.user_permissions;
        }
        
        configSchema = data;
        generateConfigUI(configSchema);
    } catch (e) {
        log(`Error loading config: ${e.message}`, 'error');
    }
}

function updateFontList(fonts) {
    const container = document.getElementById('font-list');
    if (!container) return;
    
    if (fonts.length === 0) {
        container.innerHTML = '<p style="color: #666;">暂无已上传的字体</p>';
        return;
    }
    
    let html = '<div style="display: flex; flex-direction: column; gap: 8px;">';
    fonts.forEach(font => {
        const family = font.font_family || font.filename;
        const deleteBtn = `<button data-font-id="${font.id}" data-font-name="${font.filename}" class="secondary-btn delete-font-btn" style="padding: 4px 12px; font-size: 12px; background: #F44336; color: white;">删除</button>`;
        
        html += `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; background: #F5F7FA; border-radius: 4px;">
                <span style="font-size: 13px;">${family} · ${font.filename}</span>
                ${deleteBtn}
            </div>
        `;
    });
    html += '</div>';
    container.innerHTML = html;
    container.querySelectorAll('.delete-font-btn').forEach(button => {
        button.addEventListener('click', () => deleteFont(button.dataset.fontId, button.dataset.fontName));
    });
}

async function refreshFontResources() {
    const sessionToken = localStorage.getItem('session_token');
    if (!sessionToken) {
        updateFontList([]);
        return;
    }
    try {
        const res = await fetch('/api/resources/fonts', {
            headers: {'X-Session-Token': sessionToken}
        });
        const data = await res.json();
        const fonts = data.fonts || [];
        updateFontList(fonts);
    } catch (e) {
        console.error('Failed to refresh font resources:', e);
    }
}

function updatePromptList(prompts) {
    const container = document.getElementById('prompt-list');
    if (!container) return;
    
    if (prompts.length === 0) {
        container.innerHTML = '<p style="color: #666;">暂无已上传的提示词</p>';
        return;
    }
    
    let html = '<div style="display: flex; flex-direction: column; gap: 8px;">';
    prompts.forEach(prompt => {
        // 判断是否是用户上传的提示词（路径包含 user_resources）
        const isUserPrompt = prompt.includes('user_resources');
        const displayName = isUserPrompt ? prompt.split('/').pop() : prompt;
        const deleteBtn = isUserPrompt 
            ? `<button onclick="deletePrompt('${prompt}')" class="secondary-btn" style="padding: 4px 12px; font-size: 12px; background: #F44336; color: white;">删除</button>`
            : '<span style="color: #999; font-size: 11px;">服务器提示词</span>';
        
        html += `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; background: #F5F7FA; border-radius: 4px;">
                <span style="font-family: monospace; font-size: 13px;">${displayName}</span>
                ${deleteBtn}
            </div>
        `;
    });
    html += '</div>';
    container.innerHTML = html;
}

let availableTranslators = [];
let availableLanguages = [];

async function loadTranslators() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        
        // Use authenticated mode if we have a session token
        let url = '/translators?mode=user';
        if (sessionToken) {
            url = '/translators?mode=authenticated';
            headers['X-Session-Token'] = sessionToken;
        }
        
        const res = await fetch(url, { headers });
        availableTranslators = await res.json();
        console.log('Available translators:', availableTranslators);
    } catch (e) {
        console.error('Error loading translators:', e);
    }
}

async function loadLanguages() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        
        // Use authenticated mode if we have a session token
        let url = '/languages?mode=user';
        if (sessionToken) {
            url = '/languages?mode=authenticated';
            headers['X-Session-Token'] = sessionToken;
        }
        
        const res = await fetch(url, { headers });
        availableLanguages = await res.json();
        console.log('Available languages:', availableLanguages);
    } catch (e) {
        console.error('Error loading languages:', e);
    }
}

let availableWorkflows = [];
let userSettings = {};

async function loadUserSettings() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        if (sessionToken) {
            headers['X-Session-Token'] = sessionToken;
        }
        const res = await fetch('/user/settings', { headers });
        userSettings = await res.json();
        console.log('User settings:', userSettings);
        
        // 根据设置显示/隐藏 API Keys 标签
        const apikeysTab = document.getElementById('apikeys-tab');
        const hasSessionToken = Boolean(sessionToken);
        if (apikeysTab) {
            apikeysTab.style.display = userSettings.show_env_editor && hasSessionToken ? 'block' : 'none';
        }

        if (!userSettings.show_env_editor || !hasSessionToken) {
            currentEnvVars = {};
        } else if (Object.keys(currentEnvVars).length === 0) {
            try {
                const saved = localStorage.getItem('user_env_vars');
                if (saved) {
                    const schema = getApiKeySchema();
                    const validKeys = new Set(schema.envKeys || []);
                    const parsed = JSON.parse(saved);
                    currentEnvVars = Object.fromEntries(
                        Object.entries(parsed).filter(([key, value]) => validKeys.has(key) && value)
                    );
                }
            } catch (e) {
                console.error('Failed to restore saved API keys:', e);
            }
        }
        
        // 根据权限显示/隐藏上传区域
        const fontUploadSection = document.getElementById('font-upload-section');
        if (fontUploadSection) {
            fontUploadSection.style.display = userSettings.can_upload_fonts ? 'block' : 'none';
        }
        
        const promptUploadSection = document.getElementById('prompt-upload-section');
        if (promptUploadSection) {
            promptUploadSection.style.display = userSettings.can_upload_prompts ? 'block' : 'none';
        }
    } catch (e) {
        console.error('Error loading user settings:', e);
    }
}

async function loadWorkflows() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        
        // Use authenticated mode if we have a session token
        let url = '/workflows?mode=user';
        if (sessionToken) {
            url = '/workflows?mode=authenticated';
            headers['X-Session-Token'] = sessionToken;
        }
        
        const res = await fetch(url, { headers });
        availableWorkflows = await res.json();
        console.log('Available workflows:', availableWorkflows);
        updateWorkflowSelect();
    } catch (e) {
        console.error('Error loading workflows:', e);
    }
}

function updateWorkflowSelect() {
    const workflowSelect = document.getElementById('workflow-mode');
    if (!workflowSelect) return;
    
    // 保存当前选中的值
    const currentValue = workflowSelect.value;
    
    // 清空选项
    workflowSelect.innerHTML = '';
    
    // 翻译流程映射
    const workflowMap = {
        'normal': 'Normal Translation',
        'export_trans': 'Export Translation',
        'export_raw': 'Export Original Text',
        'import_trans': 'Import Translation and Render',
        'colorize': 'Colorize Only',
        'upscale': 'Upscale Only',
        'inpaint': 'Inpaint Only'
    };
    
    // 添加允许的选项
    availableWorkflows.forEach(workflow => {
        const option = document.createElement('option');
        option.value = workflow;
        option.textContent = t(workflowMap[workflow], workflowMap[workflow]);
        workflowSelect.appendChild(option);
    });
    
    // 恢复之前的选择（如果还在列表中）
    if (availableWorkflows.includes(currentValue)) {
        workflowSelect.value = currentValue;
    }
}

// --- Presets Management ---
let availablePresets = [];
let currentPresetId = null;

async function loadPresets() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        if (!sessionToken) return;
        
        const res = await fetch('/api/presets', {
            headers: {
                'X-Session-Token': sessionToken
            }
        });
        
        if (!res.ok) {
            console.log('Presets not available');
            return;
        }
        
        const data = await res.json();
        if (data.success && data.presets) {
            availablePresets = data.presets;
            console.log('Available presets:', availablePresets);
        }
    } catch (e) {
        console.error('Error loading presets:', e);
    }
}

function createPresetSelector() {
    const wrapper = document.createElement('div');
    wrapper.className = 'form-group preset-selector';
    wrapper.style.marginBottom = '20px';
    wrapper.style.padding = '15px';
    wrapper.style.backgroundColor = '#E3F2FD';
    wrapper.style.borderRadius = '8px';
    wrapper.style.border = '1px solid #2196F3';
    
    const label = document.createElement('label');
    label.textContent = t('preset_select', '配置预设');
    label.style.fontWeight = 'bold';
    label.style.color = '#1976D2';
    wrapper.appendChild(label);
    
    const hint = document.createElement('p');
    hint.textContent = t('preset_hint', '选择预设可快速应用管理员配置的参数组合');
    hint.style.fontSize = '12px';
    hint.style.color = '#666';
    hint.style.margin = '5px 0 10px 0';
    wrapper.appendChild(hint);
    
    // 如果没有预设，显示提示信息
    if (!availablePresets || availablePresets.length === 0) {
        const noPresetHint = document.createElement('p');
        noPresetHint.textContent = t('preset_empty', '暂无可用预设');
        noPresetHint.style.color = '#999';
        noPresetHint.style.fontStyle = 'italic';
        wrapper.appendChild(noPresetHint);
        return wrapper;
    }
    
    const select = document.createElement('select');
    select.id = 'preset-select';
    select.className = 'full-width-select';
    
    // 添加默认选项
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = t('preset_none', '-- 不使用预设 --');
    select.appendChild(defaultOption);
    
    // 添加预设选项
    availablePresets.forEach(preset => {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name + (preset.description ? ` - ${preset.description}` : '');
        select.appendChild(option);
    });
    
    // 恢复之前选择的预设
    const savedPresetId = localStorage.getItem('selected_preset_id');
    if (savedPresetId && availablePresets.some(p => p.id === savedPresetId)) {
        select.value = savedPresetId;
        currentPresetId = savedPresetId;
    }
    
    select.addEventListener('change', async (e) => {
        const presetId = e.target.value;
        if (presetId) {
            await applyPreset(presetId);
        } else {
            await clearSelectedPreset();
        }
    });
    
    wrapper.appendChild(select);
    return wrapper;
}

async function applyPreset(presetId) {
    try {
        const sessionToken = localStorage.getItem('session_token');
        if (!sessionToken) return;
        
        log(t('preset_applying', '正在应用预设...'), 'info');
        
        const res = await fetch(`/api/presets/${presetId}/apply`, {
            method: 'POST',
            headers: {
                'X-Session-Token': sessionToken,
                'Content-Type': 'application/json'
            }
        });
        
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        
        const data = await res.json();
        
        if (data.success && data.config) {
            // 应用预设配置到UI
            applyConfigToUI(data.config);
            currentPresetId = presetId;
            localStorage.setItem('selected_preset_id', presetId);
            
            const preset = availablePresets.find(p => p.id === presetId);
            log(t('preset_applied', '预设已应用') + `: ${preset?.name || presetId}`, 'info');
            await updateEnvInputs();
        } else {
            throw new Error(data.error || 'Unknown error');
        }
    } catch (e) {
        console.error('Error applying preset:', e);
        log(t('preset_apply_failed', '应用预设失败') + `: ${e.message}`, 'error');
    }
}

async function clearSelectedPreset() {
    try {
        const sessionToken = localStorage.getItem('session_token');
        if (!sessionToken) return;

        const res = await fetch('/api/config/user/preset', {
            method: 'DELETE',
            headers: {
                'X-Session-Token': sessionToken,
            }
        });

        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }

        currentPresetId = null;
        localStorage.removeItem('selected_preset_id');
        log(t('preset_cleared', '已清除手动预设，回退到默认来源'), 'info');
        await updateEnvInputs();
    } catch (e) {
        console.error('Error clearing preset:', e);
        log(t('preset_clear_failed', '清除预设失败') + `: ${e.message}`, 'error');
    }
}

function applyConfigToUI(config) {
    // 遍历配置并更新UI元素
    for (const [section, content] of Object.entries(config)) {
        if (typeof content === 'object' && content !== null) {
            for (const [key, value] of Object.entries(content)) {
                const fullKey = `${section}.${key}`;
                const input = document.querySelector(`[data-key="${fullKey}"]`);
                if (input) {
                    if (input.tagName === 'SELECT') {
                        input.value = value;
                    } else if (input.type === 'checkbox') {
                        input.checked = value;
                    } else if (input.type === 'number') {
                        input.value = value;
                    } else {
                        input.value = value || '';
                    }
                }
            }
        }
    }
}

function addPresetSelectorToUI() {
    const presetSelector = createPresetSelector();
    const basicLeft = document.getElementById('basic-left');
    if (basicLeft && presetSelector) {
        // 插入到基础设置的最前面
        basicLeft.insertBefore(presetSelector, basicLeft.firstChild);
    }
}

function generateConfigUI(config) {
    const formatCapabilityLabel = (value) => {
        const translated = t(`translator_${value}`, `translator_${value}`);
        if (translated !== `translator_${value}`) {
            return translated;
        }
        return String(value)
            .replace(/_/g, ' ')
            .replace(/\b\w/g, l => l.toUpperCase());
    };

    // Helper to create inputs
    const createInput = (key, value, parentKey) => {
        const fullKey = parentKey ? `${parentKey}.${key}` : key;
        if (hiddenKeys.includes(fullKey)) return null;

        const wrapper = document.createElement('div');
        wrapper.className = 'form-group';

        const label = document.createElement('label');
        // 翻译标签：优先使用 label_{key} 格式的翻译键
        let labelText = t(`label_${key}`);
        
        // 如果没找到翻译，尝试直接用 key
        if (labelText === `label_${key}`) {
            labelText = t(key);
        }
        
        // 如果还是没找到，显示格式化的键名（将下划线替换为空格，首字母大写）
        if (labelText === key) {
            labelText = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        }

        label.textContent = labelText;
        wrapper.appendChild(label);


        let input;
        if (typeof value === 'boolean') {
            // 使用下拉框而不是复选框
            input = document.createElement('select');
            const trueOption = document.createElement('option');
            trueOption.value = 'true';
            trueOption.textContent = t('True', '是');
            const falseOption = document.createElement('option');
            falseOption.value = 'false';
            falseOption.textContent = t('False', '否');
            input.appendChild(trueOption);
            input.appendChild(falseOption);
            input.value = value ? 'true' : 'false';
            wrapper.appendChild(input);
        } else if (typeof value === 'number') {
            input = document.createElement('input');
            input.type = 'number';
            input.value = value;
            input.step = Number.isInteger(value) ? '1' : '0.01';
            wrapper.appendChild(input);
        } else {
            // String, null, or Enum - check if we have options for this key
            const options = configOptions[key];
            
            // font_family 和 high_quality_prompt_path 即使为空也显示下拉菜单
            const alwaysDropdown = (key === 'font_family' || key === 'high_quality_prompt_path');
            
            // 如果有选项列表，创建下拉框（即使当前值是null）
            if (options && Array.isArray(options) && (options.length > 0 || alwaysDropdown)) {
                // Create dropdown
                input = document.createElement('select');
                
                // 为字体和提示词添加实时刷新功能
                if (key === 'font_family' || key === 'high_quality_prompt_path') {
                    input.onfocus = async () => {
                        const currentValue = input.value;
                        try {
                            const sessionToken = localStorage.getItem('session_token');
                            const headers = {};
                            if (sessionToken) {
                                headers['X-Session-Token'] = sessionToken;
                            }
                            const res = await fetch('/config/options', { headers });
                            const newOptions = await res.json();
                            const optionsList = newOptions[key] || [];
                            
                            // 清空并重新填充
                            input.innerHTML = '';
                            
                            // 添加"不使用"选项
                            const emptyOption = document.createElement('option');
                            emptyOption.value = '';
                            emptyOption.textContent = '-- 不使用 --';
                            input.appendChild(emptyOption);
                            
                            optionsList.forEach(opt => {
                                const option = document.createElement('option');
                                option.value = opt;
                                // 显示简短文件名
                                option.textContent = opt.includes('/') ? opt.split('/').pop() : opt;
                                input.appendChild(option);
                            });
                            
                            // 恢复之前的值
                            if (currentValue === '' || optionsList.includes(currentValue)) {
                                input.value = currentValue;
                            }
                        } catch (e) {
                            console.error('Failed to refresh options:', e);
                        }
                    };
                }
                
                // 为某些参数添加空选项
                if (key === 'font_family' || key === 'high_quality_prompt_path') {
                    const emptyOption = document.createElement('option');
                    emptyOption.value = '';
                    emptyOption.textContent = '-- 不使用 --';
                    input.appendChild(emptyOption);
                }
                
                // 排版模式的翻译映射
                const layoutModeMap = {
                    'smart_scaling': t('layout_mode_smart_scaling'),
                    'strict': t('layout_mode_strict'),
                    'balloon_fill': t('layout_mode_balloon_fill')
                };
                
                options.forEach(opt => {
                    const option = document.createElement('option');
                    option.value = opt;
                    // 如果是排版模式，使用翻译
                    if (key === 'layout_mode' && layoutModeMap[opt]) {
                        option.textContent = layoutModeMap[opt];
                    } else if (key === 'translator') {
                        // 如果是翻译器，使用翻译
                        option.textContent = t(`translator_${opt}`, opt);
                    } else if (key === 'ocr' || key === 'secondary_ocr' || key === 'renderer' || key === 'colorizer') {
                        option.textContent = formatCapabilityLabel(opt);
                    } else if (key === 'target_lang' || key === 'keep_lang') {
                        // 如果是目标语言，使用翻译
                        if (key === 'keep_lang' && opt === 'none') {
                            option.textContent = t('lang_filter_disabled', 'No Filter');
                        } else {
                            option.textContent = t(`lang_${opt}`, opt);
                        }
                    } else if (key === 'ocr_vl_language_hint') {
                        // OCR 语言提示显示翻译后的全称
                        const langKey = `ocr_lang_${String(opt).toLowerCase().replace(/\s+/g, '_')}`;
                        option.textContent = t(langKey, opt);
                    } else if (key === 'font_family' || key === 'high_quality_prompt_path') {
                        // 字体和提示词显示简短文件名
                        option.textContent = opt.includes('/') ? opt.split('/').pop() : opt;
                    } else {
                        option.textContent = opt;
                    }
                    if (opt === value) {
                        option.selected = true;
                    }
                    input.appendChild(option);
                });
                
                // 如果当前值是null或空，选择空选项
                if (!value) {
                    input.value = '';
                }
                
                wrapper.appendChild(input);
            } else {
                // Regular text input
                input = document.createElement('input');
                input.type = 'text';
                input.value = value || '';
                wrapper.appendChild(input);
            }
        }

        input.dataset.key = fullKey;
        input.className = input.type === 'checkbox' ? '' : 'full-width-select';

        return wrapper;
    };

    // Distribute to tabs
    // Basic: translator, cli (some)
    // Advanced: detector, inpainter, render, upscale, colorizer
    // Options: ocr, global

    const mapToContainer = (section) => {
        if (section === 'translator') return 'basic-left';
        if (section === 'cli') return 'basic-right';
        if (section === 'detector' || section === 'inpainter') return 'advanced-left';
        if (section === 'render' || section === 'upscale' || section === 'colorizer') return 'advanced-right';
        if (section === 'ocr') return 'options-left';
        return 'options-right'; // Global
    };

    for (const [section, content] of Object.entries(config)) {
        if (typeof content === 'object' && content !== null && !Array.isArray(content)) {
            const containerId = mapToContainer(section);
            const container = document.getElementById(containerId);
            if (container) {
                // Add section header?
                // const header = document.createElement('h4');
                // header.textContent = section;
                // container.appendChild(header);

                for (const [key, value] of Object.entries(content)) {
                    const el = createInput(key, value, section);
                    if (el) container.appendChild(el);
                }
            }
        } else {
            // Global settings
            const container = document.getElementById('options-right');
            const el = createInput(section, content, '');
            if (el) container.appendChild(el);
        }
    }

    // Note: populateDropdowns() will be called after all data is loaded in init()
}

function populateDropdowns() {
    // 翻译器选项（从服务器获取，带翻译）
    const translators = availableTranslators.map(trans => {
        const transKey = `translator_${trans}`;
        return {
            value: trans,
            label: t(transKey, trans.charAt(0).toUpperCase() + trans.slice(1))
        };
    });
    
    if (translators.length > 0) {
        replaceWithSelectTranslated('translator.translator', translators);
    }

    updateEnvInputs();

    // 目标语言选项（从服务器获取，带翻译）
    const langs = availableLanguages.map(lang => {
        const langKey = `lang_${lang}`;
        return {
            value: lang,
            label: t(langKey, lang)
        };
    });
    
    if (langs.length > 0) {
        replaceWithSelectTranslated('translator.target_lang', langs);
    }

    const keepLangOptions = (configOptions['keep_lang'] || []).map(lang => {
        if (lang === 'none') {
            return {
                value: lang,
                label: t('lang_filter_disabled', 'No Filter')
            };
        }
        const langKey = `lang_${lang}`;
        return {
            value: lang,
            label: t(langKey, lang)
        };
    });

    if (keepLangOptions.length > 0) {
        replaceWithSelectTranslated('translator.keep_lang', keepLangOptions);
    }
    
    // 设置超分模型联动
    setupUpscalerDependency();
}

function setupUpscalerDependency() {
    // 监听超分模型变化
    const upscalerSelect = document.querySelector('[data-key="upscale.upscaler"]');
    const upscaleRatioSelect = document.querySelector('[data-key="upscale.upscale_ratio"]');
    
    if (!upscalerSelect || !upscaleRatioSelect) return;
    
    upscalerSelect.addEventListener('change', () => {
        updateUpscaleRatioOptions(upscalerSelect.value, upscaleRatioSelect);
    });
    
    // 初始化时也更新一次
    updateUpscaleRatioOptions(upscalerSelect.value, upscaleRatioSelect);
}

// 用于保存不同模式下的选择值
let lastRatioValue = '不使用';
let lastRealcuganModel = '不使用';

function updateUpscaleRatioOptions(upscaler, ratioSelect) {
    if (!ratioSelect) return;
    
    const currentValue = ratioSelect.value;
    const ratioOptions = ['不使用', '2', '3', '4'];
    const realcuganModels = configOptions['realcugan_model'] || [];
    
    // 保存当前值到对应的变量
    if (ratioOptions.includes(currentValue)) {
        lastRatioValue = currentValue;
    } else if (realcuganModels.includes(currentValue) || currentValue === '不使用') {
        lastRealcuganModel = currentValue;
    }
    
    ratioSelect.innerHTML = '';
    
    if (upscaler === 'realcugan') {
        // 显示 Real-CUGAN 模型列表
        const options = ['不使用', ...realcuganModels];
        
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            ratioSelect.appendChild(option);
        });
        
        // 恢复之前保存的 realcugan 模型值
        if (options.includes(lastRealcuganModel)) {
            ratioSelect.value = lastRealcuganModel;
        } else {
            ratioSelect.value = '不使用';
        }
    } else if (upscaler === 'mangajanai') {
        // 显示 MangaJaNai 特殊选项
        const mangajanaiOptions = ['x2', 'x4', 'DAT2 x4'];
        const options = ['不使用', ...mangajanaiOptions];
        
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            ratioSelect.appendChild(option);
        });
        
        // 尝试恢复之前的值，或者默认为 x4
        // 注意：这里我们可能需要一个新的 lastMangaJaNaiValue 变量，或者复用 lastRealcuganModel
        // 为了简单，我们复用 lastRealcuganModel，因为它也是存储模型名称的
        if (options.includes(lastRealcuganModel)) {
            ratioSelect.value = lastRealcuganModel;
        } else {
            ratioSelect.value = 'x4';
        }
    } else {
        // 显示普通倍率选项
        ratioOptions.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            ratioSelect.appendChild(option);
        });
        
        // 恢复之前保存的倍率值
        if (ratioOptions.includes(lastRatioValue)) {
            ratioSelect.value = lastRatioValue;
        } else {
            ratioSelect.value = '不使用';
        }
    }
}

function replaceWithSelect(fullKey, options) {
    const input = document.querySelector(`input[data-key="${fullKey}"]`);
    if (!input) return;

    const select = document.createElement('select');
    select.className = 'full-width-select';
    select.dataset.key = fullKey;

    options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt;
        option.textContent = opt;
        if (opt === input.value) option.selected = true;
        select.appendChild(option);
    });

    input.parentNode.replaceChild(select, input);
}

function replaceWithSelectTranslated(fullKey, options) {
    const input = document.querySelector(`input[data-key="${fullKey}"]`);
    if (!input) return;

    const select = document.createElement('select');
    select.className = 'full-width-select';
    select.dataset.key = fullKey;

    options.forEach(opt => {
        const option = document.createElement('option');
        option.value = opt.value;
        option.textContent = opt.label;
        if (opt.value === input.value) option.selected = true;
        select.appendChild(option);
    });

    input.parentNode.replaceChild(select, input);
}

function updateFontSelects(fonts) {
    // 查找 font_family 元素（select 或 input）
    let element = document.querySelector(`select[data-key="render.font_family"]`);
    if (!element) {
        element = document.querySelector(`input[data-key="render.font_family"]`);
    }
    
    if (!element) return;
    
    const currentValue = element.value;
    
    if (element.tagName === 'SELECT') {
        // 已经是 select，直接更新选项
        element.innerHTML = '';
        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = '-- 不使用 --';
        element.appendChild(defaultOpt);
        
        fonts.forEach(f => {
            const option = document.createElement('option');
            option.value = f;
            option.textContent = f;
            if (f === currentValue) option.selected = true;
            element.appendChild(option);
        });
    } else {
        // 是 input，替换为 select
        const select = document.createElement('select');
        select.className = 'full-width-select';
        select.dataset.key = 'render.font_family';

        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = 'Default';
        select.appendChild(defaultOpt);

        fonts.forEach(f => {
            const option = document.createElement('option');
            option.value = f;
            option.textContent = f;
            if (f === currentValue) option.selected = true;
            select.appendChild(option);
        });

        element.parentNode.replaceChild(select, element);
    }
}

function updatePromptSelects(prompts) {
    // 查找 high_quality_prompt_path 元素（可能是 input 或 select）
    let targetElement = document.querySelector(`select[data-key="translator.high_quality_prompt_path"]`);
    if (!targetElement) {
        targetElement = document.querySelector(`input[data-key="translator.high_quality_prompt_path"]`);
    }
    
    if (targetElement) {
        const currentValue = targetElement.value;
        
        // 如果已经是 select，直接更新选项
        if (targetElement.tagName === 'SELECT') {
            targetElement.innerHTML = '';
            
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = '-- 不使用 --';
            targetElement.appendChild(defaultOpt);
            
            prompts.forEach(p => {
                const option = document.createElement('option');
                option.value = p;
                option.textContent = p;
                if (p === currentValue) option.selected = true;
                targetElement.appendChild(option);
            });
        } else {
            // 如果是 input，替换为 select
            const select = document.createElement('select');
            select.className = 'full-width-select';
            select.dataset.key = 'translator.high_quality_prompt_path';

            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = '-- 不使用 --';
            select.appendChild(defaultOpt);

            prompts.forEach(p => {
                const option = document.createElement('option');
                option.value = p;
                option.textContent = p;
                if (p === currentValue) option.selected = true;
                select.appendChild(option);
            });

            targetElement.parentNode.replaceChild(select, targetElement);
        }
    }
}

// --- API Keys / Env Editor ---
let currentEnvVars = {};

function collectCurrentEnvInputs() {
    const container = document.getElementById('env-inputs-container');
    if (!container) {
        return { ...currentEnvVars };
    }

    const nextEnvVars = {};
    const inputs = container.querySelectorAll('input[data-env-key]');
    inputs.forEach(input => {
        const key = input.dataset.envKey;
        const value = input.value.trim();
        if (key && value) {
            nextEnvVars[key] = value;
        }
    });
    return nextEnvVars;
}

function getApiKeySchema() {
    return window.ApiKeySchema || { categories: [], groups: [], envKeys: [] };
}

function getApiGroupLabel(group) {
    return group.i18nKey ? t(group.i18nKey, group.name) : group.name;
}

function createEnvSummary(policy, envState) {
    const summary = document.createElement('div');
    summary.className = 'env-hint env-merge-summary';

    const lines = [];
    const presetName = envState.selected_preset_name || envState.selected_preset_id;
    if (presetName) {
        const presetSource = envState.selected_preset_source === 'group_default'
            ? '用户组默认预设'
            : '当前用户预设';
        lines.push(`当前预设：${presetName}（${presetSource}）`);
    } else {
        lines.push('当前未选中 API 预设。');
    }

    lines.push('生效顺序：用户填写 > 当前预设 > 服务器默认。继承值不会在页面明文显示。');

    if (policy.allow_server_keys === false) {
        lines.push('当前权限已禁止回落服务器默认 API Keys。');
    }
    if (policy.require_user_keys === true) {
        lines.push('当前权限要求用户必须提供 API Keys 或选择预设。');
    }

    lines.forEach(text => {
        const line = document.createElement('div');
        line.textContent = text;
        summary.appendChild(line);
    });

    return summary;
}

function getEnvFieldSourceText(key, userOverrides, envState) {
    if (userOverrides[key]) {
        return '当前使用你填写的值，会覆盖预设或服务器默认配置。';
    }

    if (envState.sources?.[key] === 'preset') {
        const presetName = envState.selected_preset_name || envState.selected_preset_id || '当前预设';
        return `当前继承自预设：${presetName}。值不会明文显示，留空可继续继承，填写可覆盖。`;
    }

    if (envState.sources?.[key] === 'server') {
        return '当前继承自服务器默认配置。值不会明文显示，留空可继续继承，填写可覆盖。';
    }

    return '';
}

function renderUserApiKeyEditor(container, mergedValues, policy, envState = {}, userOverrides = {}) {
    const schema = getApiKeySchema();
    container.innerHTML = '';

    if (!schema.groups.length) {
        container.innerHTML = `<p class="env-hint">${t('translator_no_api_keys', '当前没有可管理的 API 配置')}</p>`;
        return;
    }

    container.appendChild(createEnvSummary(policy, envState));

    schema.categories.forEach(category => {
        const groups = schema.groups.filter(group => group.category === category.id);
        if (!groups.length) {
            return;
        }

        const categoryWrapper = document.createElement('div');

        const categoryTitle = document.createElement('div');
        categoryTitle.className = 'env-category-title';
        categoryTitle.textContent = t(category.i18nKey, category.fallback);
        categoryWrapper.appendChild(categoryTitle);

        groups.forEach(group => {
            const groupCard = document.createElement('div');
            groupCard.className = 'env-group-card';

            const title = document.createElement('h4');
            title.className = 'env-group-title';
            title.textContent = getApiGroupLabel(group);
            groupCard.appendChild(title);

            if (group.note) {
                const note = document.createElement('p');
                note.className = 'env-group-note';
                note.textContent = group.note;
                groupCard.appendChild(note);
            }

            const grid = document.createElement('div');
            grid.className = 'env-group-grid';

            group.keys.forEach(item => {
                const wrapper = document.createElement('div');
                wrapper.className = 'form-group';

                const label = document.createElement('label');
                label.textContent = t(item.i18n, item.key);
                wrapper.appendChild(label);

                const input = document.createElement('input');
                input.type = item.type || 'text';
                input.className = 'full-width-select';
                input.value = mergedValues[item.key] || '';
                input.placeholder = item.placeholder || '';
                input.dataset.envKey = item.key;
                wrapper.appendChild(input);

                const sourceText = getEnvFieldSourceText(item.key, userOverrides, envState);
                if (sourceText) {
                    const sourceNote = document.createElement('div');
                    sourceNote.className = 'env-field-source';
                    sourceNote.textContent = sourceText;
                    wrapper.appendChild(sourceNote);
                }

                grid.appendChild(wrapper);
            });

            groupCard.appendChild(grid);
            categoryWrapper.appendChild(groupCard);
        });

        container.appendChild(categoryWrapper);
    });

    const savePanel = document.createElement('div');
    savePanel.className = 'env-save-panel';

    const saveBtn = document.createElement('button');
    saveBtn.className = 'primary-btn';
    saveBtn.textContent = t('save_api_keys', '保存 API 密钥');
    saveBtn.onclick = async () => {
        await saveAllEnvVars();
    };
    savePanel.appendChild(saveBtn);

    const hint = document.createElement('div');
    hint.className = 'env-save-hint';
    hint.textContent = policy.save_user_keys_to_server
        ? t('api_keys_will_be_saved', 'API 密钥将保存到服务器')
        : t('api_keys_session_only', 'API 密钥仅在本次会话中使用，不会保存到服务器');
    savePanel.appendChild(hint);

    container.appendChild(savePanel);
}

async function updateEnvInputs() {
    const container = document.getElementById('env-inputs-container');
    if (!container) {
        return;
    }

    container.innerHTML = '';

    if (!userSettings.show_env_editor) {
        return;
    }

    const sessionToken = localStorage.getItem('session_token');
    if (!sessionToken) {
        container.innerHTML = `<p class="env-hint">${t('login_required_for_api_keys', '登录后可查看和保存 API 密钥')}</p>`;
        return;
    }
    
    try {
        const schema = getApiKeySchema();
        const validKeys = new Set(schema.envKeys || []);
        let savedEnvVars = {};
        try {
            const saved = localStorage.getItem('user_env_vars');
            if (saved) {
                savedEnvVars = Object.fromEntries(
                    Object.entries(JSON.parse(saved)).filter(([key, value]) => validKeys.has(key) && value)
                );
            }
        } catch (e) {
            console.error('Failed to load from localStorage:', e);
        }
        currentEnvVars = { ...savedEnvVars };
        
        const [policyRes, envStateRes] = await Promise.all([
            fetch('/api-key-policy', {
                headers: { 'X-Session-Token': sessionToken }
            }),
            fetch('/env/effective', {
                headers: { 'X-Session-Token': sessionToken }
            })
        ]);
        const policy = await policyRes.json();
        const rawEnvState = await envStateRes.json();
        const envState = {
            ...rawEnvState,
            sources: rawEnvState.sources || {},
            merged_env_vars: {},
        };

        const presetSelect = document.getElementById('preset-select');
        if (presetSelect) {
            const effectivePresetId = envState.selected_preset_id || '';
            const canUsePreset = effectivePresetId && availablePresets.some(p => p.id === effectivePresetId);
            if (canUsePreset) {
                presetSelect.value = effectivePresetId;
                currentPresetId = effectivePresetId;
            } else if (!localStorage.getItem('selected_preset_id')) {
                presetSelect.value = '';
                currentPresetId = null;
            }
        }

        const mergedValues = { ...savedEnvVars };
        renderUserApiKeyEditor(container, mergedValues, policy, envState, savedEnvVars);
    } catch (e) {
        console.error('Error loading API key editor:', e);
        container.innerHTML = `<p class="env-hint" style="color: #f44336;">${t('error_loading_translator_config', '加载 API 配置失败')}</p>`;
    }
}

async function saveAllEnvVars() {
    const container = document.getElementById('env-inputs-container');
    if (!container) return;

    const sessionToken = localStorage.getItem('session_token');
    if (!sessionToken) {
        log(t('login_required_for_api_keys', '登录后可查看和保存 API 密钥'), 'error');
        return;
    }
    
    const inputs = container.querySelectorAll('input[data-env-key]');
    const previousEnvVars = { ...currentEnvVars };
    const payloadEnvVars = {};
    const filledEnvVars = {};
    
    inputs.forEach(input => {
        const key = input.dataset.envKey;
        const value = input.value.trim();
        if (value) {
            filledEnvVars[key] = value;
            payloadEnvVars[key] = value;
        } else if (previousEnvVars[key]) {
            payloadEnvVars[key] = '';
        }
    });

    currentEnvVars = { ...filledEnvVars };
    
    // 保存到 localStorage，页面刷新后不会丢失
    try {
        if (Object.keys(filledEnvVars).length > 0) {
            localStorage.setItem('user_env_vars', JSON.stringify(filledEnvVars));
        } else {
            localStorage.removeItem('user_env_vars');
        }
    } catch (e) {
        console.error('Failed to save to localStorage:', e);
    }
    
    try {
        const result = await fetch('/env', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Session-Token': sessionToken
            },
            body: JSON.stringify(payloadEnvVars)
        });
        
        if (!result.ok) {
            throw new Error(`HTTP ${result.status}`);
        }
        
        const data = await result.json();
        
        if (data.saved_to_server) {
            log(t('api_keys_saved_to_server', 'API 密钥已保存到服务器'), 'info');
        } else {
            log(t('api_keys_saved_session', 'API 密钥已保存（仅本次会话）'), 'info');
        }
        await updateEnvInputs();
    } catch (e) {
        console.error('Error saving env vars:', e);
        log(t('api_keys_save_failed', 'API 密钥保存失败') + ': ' + e.message, 'error');
    }
}

async function saveEnvVar(key, value) {
    // 这个函数现在只用于更新内存中的值
    currentEnvVars[key] = value;
}

// 旧的自动保存函数（已废弃，保留以防其他地方调用）
async function _oldSaveEnvVar(key, value) {
    // 更新内存中的值（用于本次翻译）
    currentEnvVars[key] = value;

    const sessionToken = localStorage.getItem('session_token');
    if (!sessionToken) {
        log(t('login_required_for_api_keys', '登录后可查看和保存 API 密钥'), 'error');
        return;
    }
    
    // 根据管理员设置决定是否保存到服务器
    // 注意：多用户场景下建议关闭 save_user_keys_to_server，避免互相覆盖
    try {
        const result = await fetch('/env', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Session-Token': sessionToken
            },
            body: JSON.stringify({ [key]: value })
        });
        const data = await result.json();
        
        if (data.saved_to_server) {
            log(`${t('env_var_saved', 'API密钥已保存')}: ${key}`, 'info');
        } else {
            // 只在本次翻译使用，不保存到服务器
            console.log(`${key} will be used for this translation only`);
        }
    } catch (e) {
        console.error('Error saving env var:', e);
        log(`${t('env_var_save_failed', 'API密钥保存失败')}: ${key}`, 'error');
    }
}

function collectConfig() {
    const config = JSON.parse(JSON.stringify(configSchema)); // Deep copy

    document.querySelectorAll('[data-key]').forEach(el => {
        const keys = el.dataset.key.split('.');
        let target = config;
        for (let i = 0; i < keys.length - 1; i++) {
            target = target[keys[i]];
        }
        const lastKey = keys[keys.length - 1];

        if (el.type === 'checkbox') {
            target[lastKey] = el.checked;
        } else if (el.type === 'number') {
            // 处理数字类型：空字符串转为 null
            const numValue = el.value.trim();
            target[lastKey] = numValue === '' ? null : parseFloat(numValue);
        } else if (el.tagName === 'SELECT' && (el.value === 'true' || el.value === 'false')) {
            // 布尔值下拉框
            target[lastKey] = el.value === 'true';
        } else {
            // 字符串类型：空字符串转为 null
            target[lastKey] = el.value.trim() === '' ? null : el.value;
        }
    });

    // 处理超分模型和倍率的关系
    const upscalerSelect = document.querySelector('[data-key="upscale.upscaler"]');
    const upscaleRatioSelect = document.querySelector('[data-key="upscale.upscale_ratio"]');
    
    if (upscalerSelect && upscaleRatioSelect && config.upscale) {
        if (upscalerSelect.value === 'realcugan') {
            // 如果选择了 realcugan，upscale_ratio 的值实际上是 realcugan_model
            const ratioValue = upscaleRatioSelect.value;
            if (ratioValue === '不使用') {
                config.upscale.upscale_ratio = null;
                config.upscale.realcugan_model = null;
            } else {
                config.upscale.realcugan_model = ratioValue;
                config.upscale.upscale_ratio = null; // realcugan 不使用普通倍率
            }
        } else if (upscalerSelect.value === 'mangajanai') {
            // 如果选择了 mangajanai
            const ratioValue = upscaleRatioSelect.value;
            if (ratioValue === '不使用') {
                config.upscale.upscale_ratio = null;
                config.upscale.mangajanai_model = null;
            } else {
                config.upscale.mangajanai_model = ratioValue;
                // 解析倍率
                if (ratioValue.includes('x2')) {
                    config.upscale.upscale_ratio = 2;
                } else if (ratioValue.includes('x4')) {
                    config.upscale.upscale_ratio = 4;
                }
                config.upscale.realcugan_model = null;
            }
        } else {
            // 其他超分模型，使用普通倍率
            const ratioValue = upscaleRatioSelect.value;
            if (ratioValue === '不使用') {
                config.upscale.upscale_ratio = null;
            } else {
                config.upscale.upscale_ratio = parseInt(ratioValue);
            }
            config.upscale.realcugan_model = null;
        }
    }
    
    return config;
}

// --- File Handling ---
let translationFiles = {}; // 存储翻译文件 {imageName: textFile}

// 检查上传限制的共享函数
function checkUploadLimits(imageFiles, currentFileCount) {
    // 使用 ?? 而不是 || 来正确处理0值（0表示不限制）
    const maxImageSizeMB = userSettings.max_image_size_mb ?? 0;
    const maxImagesPerBatch = userSettings.max_images_per_batch ?? 0;
    
    console.log(`[Upload Limits] Max size: ${maxImageSizeMB}MB, Max count: ${maxImagesPerBatch}, Current: ${currentFileCount}, Adding: ${imageFiles.length}`);
    
    // 检查数量限制（0表示不限制）
    if (maxImagesPerBatch > 0 && currentFileCount + imageFiles.length > maxImagesPerBatch) {
        const msg = currentLocale.startsWith('zh') 
            ? `超过最大上传数量限制（${maxImagesPerBatch}张），当前已有${currentFileCount}张，尝试添加${imageFiles.length}张`
            : `Exceeds max upload limit (${maxImagesPerBatch}), current: ${currentFileCount}, trying to add: ${imageFiles.length}`;
        log(msg, 'error');
        alert(msg);
        return false;
    }
    
    // 检查文件大小限制
    if (maxImageSizeMB > 0) {
        const maxSizeBytes = maxImageSizeMB * 1024 * 1024;
        console.log(`[Upload Limits] Max size in bytes: ${maxSizeBytes}`);
        
        const oversizedFiles = [];
        imageFiles.forEach(f => {
            const sizeMB = (f.size / 1024 / 1024).toFixed(2);
            console.log(`[Upload Limits] Checking file: ${f.name}, Size: ${f.size} bytes (${sizeMB}MB)`);
            if (f.size > maxSizeBytes) {
                oversizedFiles.push(f);
            }
        });
        
        if (oversizedFiles.length > 0) {
            const msg = currentLocale.startsWith('zh')
                ? `以下文件超过大小限制（${maxImageSizeMB}MB）：\n${oversizedFiles.map(f => `${f.name} (${(f.size / 1024 / 1024).toFixed(2)}MB)`).join('\n')}`
                : `Files exceed size limit (${maxImageSizeMB}MB):\n${oversizedFiles.map(f => `${f.name} (${(f.size / 1024 / 1024).toFixed(2)}MB)`).join('\n')}`;
            log(msg, 'error');
            alert(msg);
            return false;
        }
    }
    
    return true;
}

async function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    
    // 分离图片文件、PDF文件和文本文件
    const imageFiles = files.filter(f => f.type.startsWith('image/'));
    const pdfFiles = files.filter(f => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'));
    const textFiles = files.filter(f => 
        f.name.endsWith('.json') || 
        f.name.endsWith('_original.txt') || 
        f.name.endsWith('_translated.txt')
    );
    
    // 处理 PDF 文件，提取图片
    if (pdfFiles.length > 0) {
        // 先检查 PDF 文件大小限制
        const maxPdfSizeMB = userSettings.max_pdf_size_mb ?? 50; // 默认 50MB
        if (maxPdfSizeMB > 0) {
            const maxPdfSizeBytes = maxPdfSizeMB * 1024 * 1024;
            const oversizedPdfs = pdfFiles.filter(f => f.size > maxPdfSizeBytes);
            if (oversizedPdfs.length > 0) {
                const msg = currentLocale.startsWith('zh')
                    ? `以下PDF文件超过大小限制（${maxPdfSizeMB}MB）：\n${oversizedPdfs.map(f => `${f.name} (${(f.size / 1024 / 1024).toFixed(2)}MB)`).join('\n')}`
                    : `PDF files exceed size limit (${maxPdfSizeMB}MB):\n${oversizedPdfs.map(f => `${f.name} (${(f.size / 1024 / 1024).toFixed(2)}MB)`).join('\n')}`;
                log(msg, 'error');
                alert(msg);
                e.target.value = '';
                return;
            }
        }
        
        log(`${t('extracting_pdf', '正在提取PDF页面')}...`, 'info');
        for (const pdfFile of pdfFiles) {
            try {
                // 获取配额限制
                const maxImagesPerBatch = userSettings.max_images_per_batch ?? 0;
                const remainingQuota = maxImagesPerBatch > 0 ? maxImagesPerBatch - fileList.length - imageFiles.length : Infinity;
                
                const extractedImages = await extractImagesFromPDF(pdfFile, remainingQuota);
                imageFiles.push(...extractedImages);
                log(`${t('pdf_extracted', 'PDF提取完成')}: ${pdfFile.name} -> ${extractedImages.length} ${t('pages', '页')}`, 'info');
            } catch (err) {
                log(`${t('pdf_extract_failed', 'PDF提取失败')}: ${pdfFile.name} - ${err.message}`, 'error');
            }
        }
    }
    
    // 检查上传限制（图片大小和总数量）
    if (!checkUploadLimits(imageFiles, fileList.length)) {
        e.target.value = '';
        return;
    }
    
    // 添加图片文件
    imageFiles.forEach(file => {
        fileList.push(file);
        addFileToUI(file);
    });
    
    // 处理文本文件，建立与图片的关联
    textFiles.forEach(textFile => {
        // 提取基础文件名（去掉 _original.txt, _translated.txt, .json 等后缀）
        let baseName = textFile.name
            .replace(/_original\.txt$/, '')
            .replace(/_translated\.txt$/, '')
            .replace(/\.json$/, '');
        
        // 查找匹配的图片
        const matchedImage = fileList.find(img => {
            const imgBaseName = img.name.replace(/\.[^.]+$/, ''); // 去掉扩展名
            return imgBaseName === baseName;
        });
        
        if (matchedImage) {
            translationFiles[matchedImage.name] = textFile;
            log(`${t('translation_file_matched', '翻译文件已匹配')}: ${textFile.name} -> ${matchedImage.name}`, 'info');
        } else {
            log(`${t('translation_file_no_match', '未找到匹配的图片')}: ${textFile.name}`, 'warning');
        }
    });
    
    updateFileCount();
    e.target.value = ''; // Reset
}

/**
 * 从 PDF 文件中提取页面为图片
 * @param {File} pdfFile - PDF 文件
 * @param {number} maxPages - 最大提取页数限制（Infinity 表示不限制）
 * @returns {Promise<File[]>} - 提取的图片文件数组
 */
async function extractImagesFromPDF(pdfFile, maxPages = Infinity) {
    if (typeof pdfjsLib === 'undefined') {
        throw new Error('pdf.js 库未加载');
    }
    
    const arrayBuffer = await pdfFile.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
    const images = [];
    const baseName = pdfFile.name.replace(/\.pdf$/i, '');
    
    // 获取 PDF 页面数量，受配额限制
    const totalPages = pdf.numPages;
    const numPages = Math.min(totalPages, maxPages);
    
    if (numPages < totalPages) {
        log(`${t('pdf_page_limit', 'PDF页数受配额限制')}: ${pdfFile.name} - ${t('extracting', '提取')} ${numPages}/${totalPages} ${t('pages', '页')}`, 'warning');
    }
    
    for (let pageNum = 1; pageNum <= numPages; pageNum++) {
        const page = await pdf.getPage(pageNum);
        
        // 设置渲染比例（2x 以获得更好的质量）
        const scale = 2;
        const viewport = page.getViewport({ scale });
        
        // 创建 canvas
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        
        // 渲染页面到 canvas
        await page.render({
            canvasContext: context,
            viewport: viewport
        }).promise;
        
        // 将 canvas 转换为 Blob
        const blob = await new Promise(resolve => {
            canvas.toBlob(resolve, 'image/png');
        });
        
        // 创建 File 对象，文件名包含页码
        const pageNumStr = String(pageNum).padStart(3, '0');
        const fileName = `${baseName}_page_${pageNumStr}.png`;
        const file = new File([blob], fileName, { type: 'image/png' });
        images.push(file);
    }
    
    return images;
}

function handleFolderSelect(e) {
    const files = Array.from(e.target.files);
    
    // 分离图片文件和文本文件
    const imageFiles = files.filter(f => f.type.startsWith('image/'));
    const textFiles = files.filter(f => 
        f.name.endsWith('.json') || 
        f.name.endsWith('_original.txt') || 
        f.name.endsWith('_translated.txt')
    );
    
    log(`${t('folder_scan_result', '从文件夹中找到')}: ${imageFiles.length} ${t('images', '个图片')}, ${textFiles.length} ${t('translation_files', '个翻译文件')}`);
    
    // 检查上传限制
    if (!checkUploadLimits(imageFiles, fileList.length)) {
        e.target.value = '';
        return;
    }
    
    // 添加图片文件
    imageFiles.forEach(file => {
        fileList.push(file);
        addFileToUI(file);
    });
    
    // 处理文本文件
    textFiles.forEach(textFile => {
        let baseName = textFile.name
            .replace(/_original\.txt$/, '')
            .replace(/_translated\.txt$/, '')
            .replace(/\.json$/, '');
        
        const matchedImage = fileList.find(img => {
            const imgBaseName = img.name.replace(/\.[^.]+$/, '');
            return imgBaseName === baseName;
        });
        
        if (matchedImage) {
            translationFiles[matchedImage.name] = textFile;
        }
    });
    
    updateFileCount();
    e.target.value = ''; // Reset
}

function updateFileCount() {
    if (els.fileCount) {
        els.fileCount.textContent = `${fileList.length} 个文件`;
    }
}

function addFileToUI(file) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    const size = document.createElement('small');
    const removeBtn = document.createElement('span');
    size.textContent = `(${formatSize(file.size)})`;
    label.append(`${file.name} `, size);
    removeBtn.className = 'remove-btn';
    removeBtn.textContent = '✖';
    removeBtn.addEventListener('click', () => removeFile(file.name));
    li.append(label, removeBtn);
    els.fileList.appendChild(li);
}

function removeFile(name) {
    const idx = fileList.findIndex(f => f.name === name);
    if (idx !== -1) {
        fileList.splice(idx, 1);
        renderFileList();
        updateFileCount();
    }
}

function renderFileList() {
    els.fileList.innerHTML = '';
    fileList.forEach(addFileToUI);
    updateFileCount();
}

function clearFileList() {
    fileList = [];
    renderFileList();
    updateFileCount();
}

function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// --- Task Execution ---

async function startTask() {
    if (fileList.length === 0) {
        const msg = currentLocale.startsWith('zh') ? '请先添加要翻译的图片文件！' : 'Please add image files to translate!';
        log(msg, 'error');
        return;
    }

    const mode = els.workflowMode.value;
    const config = collectConfig();
    const batchSize = parseInt(config.cli?.batch_size) || 5;

    els.startBtn.disabled = true;
    const startMsg = currentLocale.startsWith('zh') ? '开始任务' : 'Task started';
    log(`${startMsg}: ${mode} - ${fileList.length} 个文件, batchSize=${batchSize}`);
    console.log(`[DEBUG] mode=${mode}, fileList.length=${fileList.length}, batchSize=${batchSize}, useBatch=${mode === 'normal' && fileList.length > 1}`);

    try {
        // 对于普通翻译模式，如果有多个文件，使用批量接口
        if (mode === 'normal' && fileList.length > 1) {
            // 按批量大小分批处理
            for (let i = 0; i < fileList.length; i += batchSize) {
                const batch = fileList.slice(i, i + batchSize);
                const batchNum = Math.floor(i / batchSize) + 1;
                const totalBatches = Math.ceil(fileList.length / batchSize);
                log(`${currentLocale.startsWith('zh') ? '批次' : 'Batch'} ${batchNum}/${totalBatches}: ${batch.length} ${currentLocale.startsWith('zh') ? '个文件' : 'files'}`, 'info');
                await processBatch(batch, config);
            }
        } else {
            // 其他模式或单个文件，逐个处理
            for (let i = 0; i < fileList.length; i++) {
                const file = fileList[i];
                const processingMsg = currentLocale.startsWith('zh') ? '正在处理' : 'Processing';
                log(`${processingMsg} [${i + 1}/${fileList.length}]: ${file.name}...`);
                await processFile(file, mode, config);
            }
        }
        
        const completeMsg = currentLocale.startsWith('zh') ? '所有任务完成！' : 'All tasks completed!';
        log(completeMsg, 'info');
    } catch (e) {
        const errorMsg = currentLocale.startsWith('zh') ? '任务出错' : 'Task error';
        log(`${errorMsg}: ${e.message}`, 'error');
    } finally {
        els.startBtn.disabled = false;
    }
}

async function processBatch(files, config) {
    const batchMsg = currentLocale.startsWith('zh') ? '批量翻译中' : 'Batch translating';
    log(`${batchMsg}: ${files.length} 个文件...`, 'info');
    
    try {
        // 将文件转换为 data URI 格式（包含前缀）
        const images = await Promise.all(files.map(async file => {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => {
                    // 保留完整的 data URI（包括 data:image/...;base64, 前缀）
                    resolve(reader.result);
                };
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
        }));
        
        // 获取原始文件名列表
        const filenames = files.map(file => file.name);
        
        // 调用批量翻译接口
        const batchSize = parseInt(config.cli?.batch_size) || 5;
        const headers = {
            'Content-Type': 'application/json'
        };
        // 添加 session token
        const sessionToken = localStorage.getItem('session_token');
        if (sessionToken) {
            headers['X-Session-Token'] = sessionToken;
        }
        // 使用 AbortController 设置超时（30分钟）
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30 * 60 * 1000);
        
        let response;
        try {
            response = await fetch('/translate/batch/images', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({
                    images: images,
                    config: config,
                    batch_size: Math.min(batchSize, files.length),
                    filenames: filenames
                }),
                signal: controller.signal
            });
        } finally {
            clearTimeout(timeoutId);
        }
        
        // 打印响应信息
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Batch translation error:', response.status, errorText);
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }
        
        // 检查自定义 header 确认是 ZIP 文件
        const xContentType = response.headers.get('x-content-type');
        console.log('X-Content-Type:', xContentType);
        
        // 先获取 blob
        const blob = await response.blob();
        console.log('Blob info - size:', blob.size, 'type:', blob.type);
        
        // 检查响应是否有效
        if (blob.size === 0) {
            throw new Error('服务器返回空响应，可能被 IDM 拦截，请在 IDM 设置中排除 localhost');
        }
        
        // 检查是否是有效的 ZIP
        if (blob.size === 0) {
            throw new Error('服务器返回空响应');
        }
        
        // 解压并显示每张图片
        if (typeof JSZip !== 'undefined') {
            try {
                const zip = new JSZip();
                const zipContent = await zip.loadAsync(blob);
                console.log('ZIP content files:', Object.keys(zipContent.files));
                
                // 遍历 ZIP 中的所有图片文件（支持多种格式）
                const imageExtensions = configOptions.image_extensions || [];
                const imageFiles = Object.keys(zipContent.files).filter(name => {
                    const lowerName = name.toLowerCase();
                    return imageExtensions.some(ext => lowerName.endsWith(ext));
                });
                console.log('Image files found:', imageFiles);
                
                for (const filename of imageFiles) {
                    // 获取图片数据
                    const imageData = await zipContent.files[filename].async('uint8array');
                    
                    // 根据扩展名确定 MIME 类型
                    const ext = filename.toLowerCase().split('.').pop();
                    const mimeTypes = {
                        'png': 'image/png',
                        'jpg': 'image/jpeg',
                        'jpeg': 'image/jpeg',
                        'jfif': 'image/jpeg',
                        'webp': 'image/webp',
                        'avif': 'image/avif',
                        'bmp': 'image/bmp',
                        'tif': 'image/tiff',
                        'tiff': 'image/tiff',
                        'heic': 'image/heic',
                        'heif': 'image/heif'
                    };
                    const mimeType = mimeTypes[ext] || 'image/png';
                    
                    // 创建带正确 MIME 类型的 blob
                    const imageBlob = new Blob([imageData], { type: mimeType });
                    const imageUrl = URL.createObjectURL(imageBlob);
                    addResult(filename, imageUrl, 'image');
                }
                
                log(`${currentLocale.startsWith('zh') ? '批量翻译完成，已添加' : 'Batch completed, added'} ${imageFiles.length} ${currentLocale.startsWith('zh') ? '张图片到结果列表' : 'images to results'}`, 'info');
            } catch (zipError) {
                console.error('Failed to extract zip:', zipError);
                log(`${currentLocale.startsWith('zh') ? '解压失败，下载ZIP文件: ' : 'Extraction failed, downloading ZIP: '}${zipError.message}`, 'warning');
                const zipFilename = `batch_translated_${Date.now()}.zip`;
                downloadBlob(blob, zipFilename);
                // 解压失败但不触发回退，因为翻译已完成
            }
        } else {
            // 如果没有 JSZip，提示用户手动下载
            log(`${currentLocale.startsWith('zh') ? '无法解压ZIP文件，请手动下载' : 'Cannot extract ZIP, please download manually'}`, 'warning');
            const zipFilename = `batch_translated_${Date.now()}.zip`;
            downloadBlob(blob, zipFilename);
        }
        
        log(`${currentLocale.startsWith('zh') ? '批量翻译完成' : 'Batch translation completed'}`, 'info');
    } catch (e) {
        console.error('Batch translation failed:', e);
        log(`${currentLocale.startsWith('zh') ? '批量翻译失败: ' : 'Batch translation failed: '}${e.message}`, 'error');
        // 不再回退到逐个处理，直接报错
    }
}

async function processFile(file, mode, config) {
    const formData = new FormData();
    formData.append('image', file);
    formData.append('config', JSON.stringify(config));
    
    // 如果用户输入了 API Keys，发送给服务器
    const requestEnvVars = collectCurrentEnvInputs();
    currentEnvVars = { ...requestEnvVars };
    if (Object.keys(requestEnvVars).length > 0) {
        formData.append('user_env_vars', JSON.stringify(requestEnvVars));
    }

    // Determine endpoint based on mode
    let endpoint = '/translate/with-form/image/stream'; // Default normal

    if (mode === 'export_trans') endpoint = '/translate/export/translated';
    else if (mode === 'export_raw') endpoint = '/translate/export/original';
    else if (mode === 'import_trans') {
        // 导入翻译并渲染：必须上传图片+JSON文件
        const translationFile = translationFiles[file.name];
        
        if (!translationFile) {
            log(`${t('import_mode_no_json', '导入翻译模式：未找到JSON文件')}: ${file.name}`, 'error');
            log(`${t('import_mode_hint', '提示：请同时上传图片和对应的JSON文件（例如：image.png 和 image.json）')}`, 'warning');
            return;
        }
        
        if (!translationFile.name.endsWith('.json')) {
            log(`${t('import_mode_json_only', '导入翻译模式：只支持JSON文件，不支持TXT文件')}: ${translationFile.name}`, 'error');
            log(`${t('import_mode_json_hint', '提示：请使用"导出原文"或"导出翻译"功能生成JSON文件')}`, 'warning');
            return;
        }
        
        endpoint = '/translate/import/json';
        formData.append('json_file', translationFile);
        log(`${t('using_translation_file', '使用翻译文件')}: ${translationFile.name}`, 'info');
    }
    else if (mode === 'colorize') endpoint = '/translate/colorize';
    else if (mode === 'upscale') endpoint = '/translate/upscale';
    else if (mode === 'inpaint') endpoint = '/translate/inpaint';

    // Special handling for stream endpoints
    if (endpoint.includes('stream') && !endpoint.includes('export')) {
        await processStream(endpoint, formData, file.name);
    } else {
        // Standard download endpoints
        const sessionToken = localStorage.getItem('session_token');
        const headers = {};
        if (sessionToken) {
            headers['X-Session-Token'] = sessionToken;
        }
        
        const res = await fetch(endpoint, {
            method: 'POST',
            body: formData,
            headers: headers
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const blob = await res.blob();
        const resultFilename = getDownloadName(file.name, mode);
        
        // 添加到结果列表（不自动下载）
        const resultUrl = URL.createObjectURL(blob);
        const resultType = resultFilename.endsWith('.zip') ? 'zip' : 'image';
        addResult(resultFilename, resultUrl, resultType);
        
        log(`${file.name} 处理完成`, 'info');
    }
}

async function processStream(endpoint, formData, filename) {
    const sessionToken = localStorage.getItem('session_token');
    const headers = {};
    if (sessionToken) {
        headers['X-Session-Token'] = sessionToken;
    }
    
    const res = await fetch(endpoint, {
        method: 'POST',
        body: formData,
        headers: headers
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    let resultChunks = [];
    let pendingBuffer = new Uint8Array(0);

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // 合并之前的不完整数据和新数据
            let buffer = new Uint8Array(pendingBuffer.length + value.length);
            buffer.set(pendingBuffer);
            buffer.set(value, pendingBuffer.length);
            pendingBuffer = new Uint8Array(0);

            // Parse custom stream format: 1 byte status, 4 bytes len, data
            let offset = 0;
            while (offset < buffer.length) {
                if (buffer.length - offset < 5) {
                    pendingBuffer = buffer.slice(offset);
                    break;
                }

                const status = buffer[offset];
                const len = (buffer[offset + 1] << 24) | (buffer[offset + 2] << 16) | (buffer[offset + 3] << 8) | buffer[offset + 4];

                if (buffer.length - offset < 5 + len) {
                    pendingBuffer = buffer.slice(offset);
                    break;
                }

                const data = buffer.slice(offset + 5, offset + 5 + len);
                offset += 5 + len;

                if (status === 0) { // Result Data
                    resultChunks.push(data);
                } else if (status === 1) { // Progress
                    try {
                        const msg = JSON.parse(new TextDecoder().decode(data));
                        // 保存 task_id 用于日志过滤
                        if (msg.task_id && msg.stage === 'task_id') {
                            currentTaskId = msg.task_id;
                            log(`开始任务: ${msg.task_id.substring(0, 8)}...`, 'info');
                        }
                        if (msg.message) log(`${msg.message}`);
                    } catch (e) {
                        console.error('Failed to parse progress message:', e);
                    }
                } else if (status === 2) { // Error
                    try {
                        const msg = JSON.parse(new TextDecoder().decode(data));
                        log(`错误: ${msg.error}`, 'error');
                        throw new Error(msg.error);
                    } catch (e) {
                        throw new Error('Translation failed');
                    }
                }
            }
        }

        if (resultChunks.length > 0) {
            const blob = new Blob(resultChunks, { type: 'image/png' });
            const resultFilename = `translated_${filename}`;
            
            // 添加到结果列表（不自动下载）
            const imageUrl = URL.createObjectURL(blob);
            addResult(resultFilename, imageUrl, 'image');
            
            log(`${filename} 翻译完成`, 'info');
        } else {
            log(`${filename} 未收到结果数据`, 'error');
        }
    } catch (error) {
        console.error('Stream processing error:', error);
        log(`${filename} 处理失败: ${error.message}`, 'error');
        throw error;
    } finally {
        // 任务完成或失败后，获取所有任务日志
        const finishedTaskId = currentTaskId;
        if (finishedTaskId) {
            // 获取该任务的所有日志（不使用时间戳过滤，获取完整日志）
            try {
                const sessionToken = localStorage.getItem('session_token');
                if (sessionToken) {
                    const url = `/api/logs?limit=500&task_id=${finishedTaskId}`;
                    const res = await fetch(url, {
                        headers: { 'X-Session-Token': sessionToken }
                    });
                    if (res.status === 401) {
                        handleLogPollingAuthFailure();
                    } else if (res.ok) {
                        const logs = await res.json();
                        if (logs.length > 0) {
                            // 获取所有未显示的日志（使用时间戳过滤）
                            const newLogs = lastLogTimestamp
                                ? logs.filter(l => new Date(l.timestamp) > new Date(lastLogTimestamp))
                                : logs;
                            if (newLogs.length > 0) {
                                log(`--- 详细日志 (${newLogs.length} 条) ---`, 'info', true);
                                newLogs.forEach(l => {
                                    log(`[${l.level}] ${l.message}`, l.level.toLowerCase(), false);
                                });
                            }
                        }
                    }
                }
            } catch (e) {
                console.error('Failed to fetch final logs:', e);
            }
        }
        currentTaskId = null;
        lastLogTimestamp = null;
        logPollingAuthErrorShown = false;
    }
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // 不立即释放 URL，因为结果列表需要使用它
    // URL 会在清空结果或删除单个结果时释放
}

function getDownloadName(original, mode) {
    const base = original.split('.')[0];
    if (mode === 'export_trans') return `${base}_translated.zip`;
    if (mode === 'export_raw') return `${base}_original.zip`;
    return `result_${original}`;
}

// --- Utils ---

function switchTab(tabId) {
    els.tabs.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    els.tabPanes.forEach(pane => {
        pane.classList.toggle('active', pane.id === tabId);
    });
}

// --- Log Handling ---

let lastLogTimestamp = null;
let currentTaskId = null; // 当前正在处理的任务ID
let logPollingTimer = null;
let logPollingAuthErrorShown = false;

function stopLogPolling() {
    if (logPollingTimer) {
        clearInterval(logPollingTimer);
        logPollingTimer = null;
    }
}

function handleLogPollingAuthFailure() {
    stopLogPolling();
    if (logPollingAuthErrorShown) {
        return;
    }
    logPollingAuthErrorShown = true;
    log('登录状态已过期，已停止实时日志轮询。当前任务可能仍在继续，请重新登录后再查看日志。', 'warning');
}

function startLogPolling() {
    if (logPollingTimer) {
        return;
    }

    logPollingTimer = setInterval(async () => {
        try {
            // 只有在有当前任务时才轮询日志
            if (!currentTaskId) {
                return; // 没有任务，不轮询
            }

            const sessionToken = localStorage.getItem('session_token');
            if (!sessionToken) {
                handleLogPollingAuthFailure();
                return;
            }
            
            // 获取当前任务的日志（增加限制到200条以获取更多详细日志）
            const url = `/api/logs?limit=200&task_id=${currentTaskId}`;
            const res = await fetch(url, {
                headers: {
                    'X-Session-Token': sessionToken
                }
            });
            if (res.status === 401) {
                handleLogPollingAuthFailure();
                return;
            }
            if (!res.ok) {
                return;
            }
            const logs = await res.json();

            if (logs.length > 0) {
                // Filter new logs
                const newLogs = lastLogTimestamp
                    ? logs.filter(l => new Date(l.timestamp) > new Date(lastLogTimestamp))
                    : logs;

                if (newLogs.length > 0) {
                    newLogs.forEach(l => {
                        // Avoid duplicates if possible, though timestamp check helps
                        log(`[${l.level}] ${l.message}`, l.level.toLowerCase(), false);
                    });
                    lastLogTimestamp = newLogs[newLogs.length - 1].timestamp;
                }
            }
        } catch (e) {
            // console.error("Log poll failed", e);
        }
    }, 500); // Poll every 500ms for more responsive log updates
}

function log(msg, type = 'normal', isLocal = true) {
    // If local log (from script), just append.
    // If remote log (from polling), append.
    // We might want to distinguish them visually?

    const div = document.createElement('div');
    div.className = `log-entry ${type}`;

    // If msg already has timestamp (from remote), use it? 
    // Or just use current time for local.
    const timeStr = isLocal ? new Date().toLocaleTimeString() : '';
    div.textContent = isLocal ? `[${timeStr}] ${msg}` : msg;

    els.logBox.appendChild(div);
    els.logBox.scrollTop = els.logBox.scrollHeight;
}

function exportConfig() {
    const config = collectConfig();
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    downloadBlob(blob, 'config.json');
}

function importConfig() {
    const file = els.configInput.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const config = JSON.parse(e.target.result);
            // Update UI
            // This requires mapping back to inputs. 
            // Simplified: just reload page or re-generate UI?
            // Re-generating UI is better.
            els.logBox.innerHTML = '';
            document.getElementById('basic-left').innerHTML = '';
            document.getElementById('basic-right').innerHTML = '';
            document.getElementById('advanced-left').innerHTML = '';
            document.getElementById('advanced-right').innerHTML = '';
            document.getElementById('options-left').innerHTML = '';
            document.getElementById('options-right').innerHTML = '';

            configSchema = config; // Update schema with values
            generateConfigUI(config);
            log('配置已导入', 'info');
        } catch (err) {
            log('导入配置失败', 'error');
        }
    };
    reader.readAsText(file);
    els.configInput.value = '';
}


// --- Upload Handlers ---

async function handleFontUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    const sessionToken = localStorage.getItem('session_token');
    const headers = {};
    if (sessionToken) {
        headers['X-Session-Token'] = sessionToken;
    }
    
    try {
        // 使用用户资源上传端点
        const res = await fetch('/api/resources/fonts', {
            method: 'POST',
            headers: headers,
            body: formData
        });
        
        if (res.ok) {
            log(`${t('font_uploaded', '字体上传成功')}: ${file.name}`, 'info');
            // 重新加载配置选项以获取新字体
            await loadConfigOptions();
            // 更新字体下拉框和管理列表
            if (configOptions['font_family']) {
                updateFontSelects(configOptions['font_family']);
            }
            await refreshFontResources();
        } else {
            const error = await res.text();
            log(`${t('font_upload_failed', '字体上传失败')}: ${error}`, 'error');
        }
    } catch (error) {
        log(`${t('font_upload_error', '字体上传错误')}: ${error.message}`, 'error');
    }
    
    e.target.value = ''; // Reset
}

async function handlePromptUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    const sessionToken = localStorage.getItem('session_token');
    const headers = {};
    if (sessionToken) {
        headers['X-Session-Token'] = sessionToken;
    }
    
    try {
        // 使用用户资源上传端点
        const res = await fetch('/api/resources/prompts', {
            method: 'POST',
            headers: headers,
            body: formData
        });
        
        if (res.ok) {
            log(`${t('prompt_uploaded', '提示词上传成功')}: ${file.name}`, 'info');
            // 重新加载配置选项以获取新提示词
            await loadConfigOptions();
            // 更新提示词下拉框和管理列表
            if (configOptions['high_quality_prompt_path']) {
                updatePromptSelects(configOptions['high_quality_prompt_path']);
                updatePromptList(configOptions['high_quality_prompt_path']);
            }
        } else {
            const error = await res.text();
            log(`${t('prompt_upload_failed', '提示词上传失败')}: ${error}`, 'error');
        }
    } catch (error) {
        log(`${t('prompt_upload_error', '提示词上传错误')}: ${error.message}`, 'error');
    }
    
    e.target.value = ''; // Reset
}

async function deleteFont(resourceId, filename) {
    const displayName = filename;
    if (!confirm(`确定要删除字体 "${displayName}" 吗？`)) {
        return;
    }
    
    const sessionToken = localStorage.getItem('session_token');
    const headers = {};
    if (sessionToken) {
        headers['X-Session-Token'] = sessionToken;
    }
    
    try {
        const res = await fetch(`/api/resources/fonts/${encodeURIComponent(resourceId)}`, {
            method: 'DELETE',
            headers: headers
        });
        
        if (res.ok) {
            log(`${t('font_deleted', '字体删除成功')}: ${displayName}`, 'info');
            // 重新加载配置选项并更新UI
            await loadConfigOptions();
            if (configOptions['font_family']) {
                updateFontSelects(configOptions['font_family']);
            }
            await refreshFontResources();
        } else {
            const error = await res.text();
            log(`${t('font_delete_failed', '字体删除失败')}: ${error}`, 'error');
        }
    } catch (error) {
        log(`${t('font_delete_error', '字体删除错误')}: ${error.message}`, 'error');
    }
}

async function deletePrompt(promptPath) {
    const displayName = promptPath.split('/').pop();
    if (!confirm(`确定要删除提示词 "${displayName}" 吗？`)) {
        return;
    }
    
    const sessionToken = localStorage.getItem('session_token');
    const headers = {};
    if (sessionToken) {
        headers['X-Session-Token'] = sessionToken;
    }
    
    try {
        // 用户提示词通过文件名删除
        const filename = promptPath.split('/').pop();
        const res = await fetch(`/api/resources/prompts/by-name/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
            headers: headers
        });
        
        if (res.ok) {
            log(`${t('prompt_deleted', '提示词删除成功')}: ${displayName}`, 'info');
            // 重新加载配置选项并更新UI
            await loadConfigOptions();
            if (configOptions['high_quality_prompt_path']) {
                updatePromptSelects(configOptions['high_quality_prompt_path']);
                updatePromptList(configOptions['high_quality_prompt_path']);
            }
        } else {
            const error = await res.text();
            log(`${t('prompt_delete_failed', '提示词删除失败')}: ${error}`, 'error');
        }
    } catch (error) {
        log(`${t('prompt_delete_error', '提示词删除错误')}: ${error.message}`, 'error');
    }
}

// --- Results Management ---
let resultsList = [];

function loadResults() {
    try {
        const saved = localStorage.getItem('translationResults');
        if (saved) {
            resultsList = JSON.parse(saved);
            renderResults();
        }
    } catch (e) {
        console.error('Error loading results:', e);
        resultsList = [];
    }
}

function saveResults() {
    try {
        localStorage.setItem('translationResults', JSON.stringify(resultsList));
    } catch (e) {
        console.error('Error saving results:', e);
    }
}

function addResult(filename, imageData, type = 'image') {
    const result = {
        id: Date.now() + Math.random(),
        filename,
        imageData,
        type,
        timestamp: new Date().toISOString()
    };
    resultsList.push(result);
    saveResults();
    renderResults();
}

function renderResults() {
    const container = document.getElementById('results-list');
    const emptyHint = document.getElementById('results-empty');
    const expandResultsBtn = document.getElementById('expand-results-btn');
    const downloadAllBtn = document.getElementById('download-all-btn');
    const clearResultsBtn = document.getElementById('clear-results-btn');
    
    if (!container) return;
    
    if (resultsList.length === 0) {
        container.innerHTML = '';
        emptyHint.style.display = 'block';
        if (expandResultsBtn) expandResultsBtn.style.display = 'none';
        downloadAllBtn.style.display = 'none';
        clearResultsBtn.style.display = 'none';
        return;
    }
    
    emptyHint.style.display = 'none';
    
    // 只有当有图片类型的结果时才显示展开按钮
    const hasImages = resultsList.some(r => r.type === 'image');
    if (expandResultsBtn) expandResultsBtn.style.display = hasImages ? 'inline-block' : 'none';
    
    downloadAllBtn.style.display = 'inline-block';
    clearResultsBtn.style.display = 'inline-block';
    
    container.innerHTML = '';
    
    // 按时间倒序显示（最新的在前）
    const sortedResults = [...resultsList].reverse();
    
    sortedResults.forEach(result => {
        const li = document.createElement('li');
        li.className = 'result-item';
        
        const info = document.createElement('div');
        info.className = 'result-info';
        
        const icon = document.createElement('span');
        icon.className = 'result-icon';
        icon.textContent = result.type === 'zip' ? '📦' : '🖼️';
        
        const name = document.createElement('span');
        name.className = 'result-name';
        name.textContent = result.filename;
        name.title = result.filename;
        
        const time = document.createElement('span');
        time.className = 'result-time';
        time.textContent = formatTime(result.timestamp);
        
        info.appendChild(icon);
        info.appendChild(name);
        info.appendChild(time);
        
        const actions = document.createElement('div');
        actions.className = 'result-actions';
        
        // 查看按钮
        const viewBtn = document.createElement('button');
        viewBtn.className = 'result-btn';
        viewBtn.textContent = t('view', '查看');
        viewBtn.onclick = () => viewResult(result);
        
        // 下载按钮
        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'result-btn';
        downloadBtn.textContent = t('download', '下载');
        downloadBtn.onclick = () => downloadResult(result);
        
        // 删除按钮
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'result-btn delete-btn';
        deleteBtn.textContent = '×';
        deleteBtn.title = t('delete', '删除');
        deleteBtn.onclick = () => deleteResult(result.id);
        
        actions.appendChild(viewBtn);
        actions.appendChild(downloadBtn);
        actions.appendChild(deleteBtn);
        
        li.appendChild(info);
        li.appendChild(actions);
        container.appendChild(li);
    });
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    // 小于1分钟
    if (diff < 60000) {
        return t('just_now', '刚刚');
    }
    
    // 小于1小时
    if (diff < 3600000) {
        const minutes = Math.floor(diff / 60000);
        return currentLocale.startsWith('zh') ? `${minutes}分钟前` : `${minutes}m ago`;
    }
    
    // 小于24小时
    if (diff < 86400000) {
        const hours = Math.floor(diff / 3600000);
        return currentLocale.startsWith('zh') ? `${hours}小时前` : `${hours}h ago`;
    }
    
    // 显示日期时间
    return date.toLocaleString(currentLocale.replace('_', '-'));
}

function viewResult(result) {
    // 在新标签页打开
    window.open(result.imageData, '_blank');
}

function downloadResult(result) {
    const a = document.createElement('a');
    a.href = result.imageData;
    a.download = result.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function deleteResult(id) {
    const result = resultsList.find(r => r.id === id);
    if (result && result.imageData && result.imageData.startsWith('blob:')) {
        URL.revokeObjectURL(result.imageData);
    }
    
    resultsList = resultsList.filter(r => r.id !== id);
    saveResults();
    renderResults();
}

async function downloadAllResults() {
    if (resultsList.length === 0) return;
    
    try {
        log(t('packing_results', '正在打包所有结果...'), 'info');
        
        // 动态导入 JSZip
        if (typeof JSZip === 'undefined') {
            // 如果没有加载 JSZip，使用简单的逐个下载
            for (const result of resultsList) {
                downloadResult(result);
                await new Promise(resolve => setTimeout(resolve, 500)); // 延迟避免浏览器阻止
            }
            log(t('download_complete', '下载完成'), 'info');
            return;
        }
        
        const zip = new JSZip();
        
        // 添加所有文件到 ZIP
        for (const result of resultsList) {
            try {
                const response = await fetch(result.imageData);
                const blob = await response.blob();
                zip.file(result.filename, blob);
            } catch (e) {
                console.error(`Failed to add ${result.filename} to zip:`, e);
            }
        }
        
        // 生成 ZIP 文件
        const zipBlob = await zip.generateAsync({ type: 'blob' });
        
        // 下载 ZIP
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
        const zipFilename = `translation_results_${timestamp}.zip`;
        
        const url = URL.createObjectURL(zipBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = zipFilename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        log(t('download_complete', '下载完成'), 'info');
    } catch (e) {
        console.error('Failed to pack results:', e);
        log(t('download_failed', '下载失败') + ': ' + e.message, 'error');
        
        // 回退到逐个下载
        for (const result of resultsList) {
            downloadResult(result);
            await new Promise(resolve => setTimeout(resolve, 500));
        }
    }
}

function clearResults() {
    if (confirm(t('confirm_clear_results', '确定要清空所有翻译结果吗？'))) {
        // 释放所有 blob URLs
        resultsList.forEach(result => {
            if (result.imageData && result.imageData.startsWith('blob:')) {
                URL.revokeObjectURL(result.imageData);
            }
        });
        
        resultsList = [];
        saveResults();
        renderResults();
        log(t('results_cleared', '翻译结果已清空'), 'info');
    }
}


// --- Image Viewer ---
let currentPreviewIndex = -1;

function openImageViewer() {
    const modal = document.getElementById('image-viewer-modal');
    modal.style.display = 'flex';
    renderThumbnails();
}

function closeImageViewer() {
    const modal = document.getElementById('image-viewer-modal');
    modal.style.display = 'none';
    currentPreviewIndex = -1;
}

function renderThumbnails() {
    const container = document.getElementById('thumbnails-container');
    container.innerHTML = '';
    
    // 只显示图片类型的结果
    const imageResults = resultsList.filter(r => r.type === 'image');
    
    if (imageResults.length === 0) {
        container.innerHTML = '<div style="padding: 20px; text-align: center; color: #999;">暂无图片</div>';
        return;
    }
    
    imageResults.forEach((result, index) => {
        const item = document.createElement('div');
        item.className = 'thumbnail-item';
        item.onclick = () => showPreview(index);
        
        const img = document.createElement('img');
        img.src = result.imageData;
        img.alt = result.filename;
        
        const icon = document.createElement('div');
        icon.className = 'thumbnail-icon';
        icon.textContent = '🖼️';
        
        const name = document.createElement('div');
        name.className = 'thumbnail-name';
        name.textContent = result.filename;
        name.title = result.filename;
        
        item.appendChild(img);
        item.appendChild(icon);
        item.appendChild(name);
        container.appendChild(item);
    });
    
    // 自动显示第一张图片
    if (imageResults.length > 0) {
        showPreview(0);
    }
}

function showPreview(index) {
    const imageResults = resultsList.filter(r => r.type === 'image');
    if (index < 0 || index >= imageResults.length) return;
    
    currentPreviewIndex = index;
    const result = imageResults[index];
    
    // 更新缩略图选中状态
    const thumbnails = document.querySelectorAll('.thumbnail-item');
    thumbnails.forEach((thumb, i) => {
        thumb.classList.toggle('active', i === index);
    });
    
    // 显示大图
    const previewContainer = document.getElementById('preview-container');
    previewContainer.innerHTML = '';
    
    const img = document.createElement('img');
    img.src = result.imageData;
    img.alt = result.filename;
    previewContainer.appendChild(img);
    
    // 更新文件名和下载按钮
    document.getElementById('preview-filename').textContent = result.filename;
    const downloadBtn = document.getElementById('preview-download-btn');
    downloadBtn.style.display = 'inline-block';
    downloadBtn.onclick = () => downloadResult(result);
}

// 键盘导航
document.addEventListener('keydown', (e) => {
    const modal = document.getElementById('image-viewer-modal');
    if (modal.style.display !== 'flex') return;
    
    const imageResults = resultsList.filter(r => r.type === 'image');
    
    if (e.key === 'ArrowLeft' && currentPreviewIndex > 0) {
        showPreview(currentPreviewIndex - 1);
    } else if (e.key === 'ArrowRight' && currentPreviewIndex < imageResults.length - 1) {
        showPreview(currentPreviewIndex + 1);
    } else if (e.key === 'Escape') {
        closeImageViewer();
    }
});

// 点击模态框背景关闭
document.getElementById('image-viewer-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'image-viewer-modal') {
        closeImageViewer();
    }
});

// 翻译历史功能已移至 /static/js/history-gallery.js


// ========== 移动端菜单功能 ==========
let mobileMenuOpen = false;

/**
 * 防抖函数
 * @param {Function} func - 要防抖的函数
 * @param {number} wait - 等待时间（毫秒）
 * @returns {Function} 防抖后的函数
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 检测是否为移动端视图
 * @returns {boolean} 是否为移动端视图（宽度 < 768px）
 */
function isMobileView() {
    return window.innerWidth < 768;
}

/**
 * 检测是否为平板视图
 * @returns {boolean} 是否为平板视图（768px <= 宽度 <= 1024px）
 */
function isTabletView() {
    return window.innerWidth >= 768 && window.innerWidth <= 1024;
}

/**
 * 初始化移动端菜单功能
 * 在 init() 函数中调用
 * 移动端布局：左侧面板（文件操作）在主页面，右侧面板（设置）在抽屉中
 */
function initMobileMenu() {
    const menuBtn = document.getElementById('mobile-menu-btn');
    const overlay = document.getElementById('mobile-overlay');
    const rightPanel = document.querySelector('.right-panel');
    
    if (!menuBtn || !overlay || !rightPanel) {
        console.warn('Mobile menu elements not found');
        return;
    }
    
    // 菜单按钮点击事件 - 打开设置面板
    menuBtn.addEventListener('click', toggleMobileMenu);
    
    // 遮罩层点击关闭
    overlay.addEventListener('click', closeMobileMenu);
    
    // 监听窗口大小变化
    window.addEventListener('resize', debounce(handleResize, 250));
    
    // 初始化时检查是否需要显示菜单按钮
    updateMobileMenuVisibility();
    
    // 阻止右侧面板内的点击事件冒泡到遮罩层
    rightPanel.addEventListener('click', (e) => e.stopPropagation());
}

/**
 * 切换移动端菜单（设置面板）
 */
function toggleMobileMenu() {
    if (mobileMenuOpen) {
        closeMobileMenu();
    } else {
        openMobileMenu();
    }
}

/**
 * 打开移动端菜单（设置面板）
 */
function openMobileMenu() {
    const rightPanel = document.querySelector('.right-panel');
    const overlay = document.getElementById('mobile-overlay');
    const menuBtn = document.getElementById('mobile-menu-btn');
    
    if (rightPanel) rightPanel.classList.add('open');
    if (overlay) overlay.classList.add('active');
    if (menuBtn) menuBtn.innerHTML = '✕';
    
    mobileMenuOpen = true;
    
    // 防止背景滚动
    document.body.style.overflow = 'hidden';
}

/**
 * 关闭移动端菜单（设置面板）
 */
function closeMobileMenu() {
    const rightPanel = document.querySelector('.right-panel');
    const overlay = document.getElementById('mobile-overlay');
    const menuBtn = document.getElementById('mobile-menu-btn');
    
    if (rightPanel) rightPanel.classList.remove('open');
    if (overlay) overlay.classList.remove('active');
    if (menuBtn) menuBtn.innerHTML = '⚙️';
    
    mobileMenuOpen = false;
    
    // 恢复背景滚动
    document.body.style.overflow = '';
}

/**
 * 处理窗口大小变化
 */
function handleResize() {
    updateMobileMenuVisibility();
    
    // 如果从移动端切换到桌面端，关闭菜单
    if (window.innerWidth >= 768 && mobileMenuOpen) {
        closeMobileMenu();
    }
}

/**
 * 更新移动端菜单按钮可见性
 */
function updateMobileMenuVisibility() {
    const menuBtn = document.getElementById('mobile-menu-btn');
    if (menuBtn) {
        menuBtn.style.display = isMobileView() ? 'flex' : 'none';
    }
}

// ========== 移动端图片查看器增强 ==========
let imageViewerScale = 1;
let imageViewerLastDistance = 0;
let imageViewerLastTap = 0;

/**
 * 初始化移动端图片查看器
 * 支持双指缩放和双击重置缩放
 */
function initMobileImageViewer() {
    const previewContainer = document.getElementById('preview-container');
    if (!previewContainer) {
        console.warn('Preview container not found for mobile image viewer');
        return;
    }
    
    // 双指缩放手势
    previewContainer.addEventListener('touchstart', handleImageTouchStart, { passive: false });
    previewContainer.addEventListener('touchmove', handleImageTouchMove, { passive: false });
    previewContainer.addEventListener('touchend', handleImageTouchEnd, { passive: false });
    
    console.log('Mobile image viewer initialized');
}

/**
 * 处理图片触摸开始事件
 * @param {TouchEvent} e - 触摸事件
 */
function handleImageTouchStart(e) {
    if (e.touches.length === 2) {
        // 双指触摸，记录初始距离
        imageViewerLastDistance = getDistance(e.touches[0], e.touches[1]);
    }
}

/**
 * 处理图片触摸移动事件（双指缩放）
 * @param {TouchEvent} e - 触摸事件
 */
function handleImageTouchMove(e) {
    if (e.touches.length === 2) {
        e.preventDefault();
        
        const distance = getDistance(e.touches[0], e.touches[1]);
        if (imageViewerLastDistance > 0) {
            const delta = distance / imageViewerLastDistance;
            imageViewerScale = Math.min(Math.max(imageViewerScale * delta, 0.5), 3);
            
            const previewContainer = document.getElementById('preview-container');
            const img = previewContainer?.querySelector('img');
            if (img) {
                img.style.transform = `scale(${imageViewerScale})`;
                img.style.transformOrigin = 'center center';
            }
        }
        
        imageViewerLastDistance = distance;
    }
}

/**
 * 处理图片触摸结束事件（双击重置缩放）
 * @param {TouchEvent} e - 触摸事件
 */
function handleImageTouchEnd(e) {
    // 重置双指缩放距离
    if (e.touches.length < 2) {
        imageViewerLastDistance = 0;
    }
    
    // 双击检测（仅单指触摸时）
    if (e.touches.length === 0 && e.changedTouches.length === 1) {
        const currentTime = new Date().getTime();
        const tapLength = currentTime - imageViewerLastTap;
        
        if (tapLength < 300 && tapLength > 0) {
            // 双击，重置缩放
            resetImageViewerZoom();
        }
        imageViewerLastTap = currentTime;
    }
}

/**
 * 重置图片查看器缩放
 */
function resetImageViewerZoom() {
    imageViewerScale = 1;
    const previewContainer = document.getElementById('preview-container');
    const img = previewContainer?.querySelector('img');
    if (img) {
        img.style.transform = 'scale(1)';
    }
}

/**
 * 计算两个触摸点之间的距离
 * @param {Touch} touch1 - 第一个触摸点
 * @param {Touch} touch2 - 第二个触摸点
 * @returns {number} 两点之间的距离
 */
function getDistance(touch1, touch2) {
    const dx = touch1.clientX - touch2.clientX;
    const dy = touch1.clientY - touch2.clientY;
    return Math.sqrt(dx * dx + dy * dy);
}
