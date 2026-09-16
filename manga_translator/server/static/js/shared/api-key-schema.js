(function () {
    const categories = [
        { id: 'translation', i18nKey: 'label_translator', fallback: '翻译' },
        { id: 'ocr', i18nKey: 'label_ocr', fallback: 'OCR' },
        { id: 'colorizer', i18nKey: 'label_colorizer', fallback: '上色' },
        { id: 'renderer', i18nKey: 'label_renderer', fallback: '渲染' },
    ];

    const groups = [
        {
            id: 'translation-openai',
            category: 'translation',
            name: 'OpenAI / ChatGPT',
            i18nKey: 'translator_openai',
            keys: [
                { key: 'OPENAI_API_KEY', i18n: 'label_OPENAI_API_KEY', type: 'password', placeholder: 'sk-...' },
                { key: 'OPENAI_API_BASE', i18n: 'label_OPENAI_API_BASE', type: 'text', placeholder: 'https://api.openai.com/v1' },
                { key: 'OPENAI_MODEL', i18n: 'label_OPENAI_MODEL', type: 'text', placeholder: 'gpt-4o' },
            ],
        },
        {
            id: 'translation-gemini',
            category: 'translation',
            name: 'Google Gemini',
            i18nKey: 'translator_gemini',
            note: 'Google Cloud / Vertex 相关 API Key 也可直接填在这里；Base URL 保持默认官方地址即可，无需修改。',
            keys: [
                { key: 'GEMINI_API_KEY', i18n: 'label_GEMINI_API_KEY', type: 'password', placeholder: 'AIza...' },
                { key: 'GEMINI_API_BASE', i18n: 'label_GEMINI_API_BASE', type: 'text', placeholder: 'https://generativelanguage.googleapis.com' },
                { key: 'GEMINI_MODEL', i18n: 'label_GEMINI_MODEL', type: 'text', placeholder: 'gemini-1.5-flash-002' },
            ],
        },
        {
            id: 'translation-sakura',
            category: 'translation',
            name: 'Sakura',
            i18nKey: 'translator_sakura',
            note: 'Sakura 使用固定兼容密钥，只需要配置 API 地址和词典路径。',
            keys: [
                { key: 'SAKURA_API_BASE', i18n: 'label_SAKURA_API_BASE', type: 'text', placeholder: 'http://127.0.0.1:8080/v1' },
                { key: 'SAKURA_DICT_PATH', i18n: 'label_SAKURA_DICT_PATH', type: 'text', placeholder: './dict/sakura_dict.txt' },
            ],
        },
        {
            id: 'translation-aliyun',
            category: 'translation',
            name: 'Aliyun Translate',
            i18nKey: 'translator_aliyun',
            note: '使用阿里云机器翻译通用版 TranslateGeneral；需要 AccessKey ID 和 AccessKey Secret。免费额度以阿里云控制台当前规则为准。',
            keys: [
                { key: 'ALIYUN_ACCESS_KEY_ID', i18n: 'label_ALIYUN_ACCESS_KEY_ID', type: 'text', placeholder: 'LTAI...' },
                { key: 'ALIYUN_ACCESS_KEY_SECRET', i18n: 'label_ALIYUN_ACCESS_KEY_SECRET', type: 'password', placeholder: 'AccessKey Secret' },
                { key: 'ALIYUN_API_BASE', i18n: 'label_ALIYUN_API_BASE', type: 'text', placeholder: 'https://mt.cn-hangzhou.aliyuncs.com' },
            ],
        },
        {
            id: 'ocr-openai',
            category: 'ocr',
            name: 'OpenAI OCR',
            note: '需单独配置，不会回落到翻译分组。',
            keys: [
                { key: 'OCR_OPENAI_API_KEY', i18n: 'label_OCR_OPENAI_API_KEY', type: 'password', placeholder: 'sk-...' },
                { key: 'OCR_OPENAI_API_BASE', i18n: 'label_OCR_OPENAI_API_BASE', type: 'text', placeholder: 'https://api.openai.com/v1' },
                { key: 'OCR_OPENAI_MODEL', i18n: 'label_OCR_OPENAI_MODEL', type: 'text', placeholder: 'gpt-4o' },
            ],
        },
        {
            id: 'ocr-gemini',
            category: 'ocr',
            name: 'Gemini OCR',
            note: '需单独配置，不会回落到翻译分组。Google Cloud / Vertex 相关 API Key 也直接填在这里；Base URL 保持默认官方地址即可，无需修改。',
            keys: [
                { key: 'OCR_GEMINI_API_KEY', i18n: 'label_OCR_GEMINI_API_KEY', type: 'password', placeholder: 'AIza...' },
                { key: 'OCR_GEMINI_API_BASE', i18n: 'label_OCR_GEMINI_API_BASE', type: 'text', placeholder: 'https://generativelanguage.googleapis.com' },
                { key: 'OCR_GEMINI_MODEL', i18n: 'label_OCR_GEMINI_MODEL', type: 'text', placeholder: 'gemini-1.5-flash' },
            ],
        },
        {
            id: 'color-openai',
            category: 'colorizer',
            name: 'OpenAI Colorizer',
            note: '需单独配置，不会回落到翻译分组。',
            keys: [
                { key: 'COLOR_OPENAI_API_KEY', i18n: 'label_COLOR_OPENAI_API_KEY', type: 'password', placeholder: 'sk-...' },
                { key: 'COLOR_OPENAI_API_BASE', i18n: 'label_COLOR_OPENAI_API_BASE', type: 'text', placeholder: 'https://api.openai.com/v1' },
                { key: 'COLOR_OPENAI_MODEL', i18n: 'label_COLOR_OPENAI_MODEL', type: 'text', placeholder: 'gpt-image-1' },
            ],
        },
        {
            id: 'color-gemini',
            category: 'colorizer',
            name: 'Gemini Colorizer',
            note: '需单独配置，不会回落到翻译分组。Google Cloud / Vertex 相关 API Key 也直接填在这里；Base URL 保持默认官方地址即可，无需修改。',
            keys: [
                { key: 'COLOR_GEMINI_API_KEY', i18n: 'label_COLOR_GEMINI_API_KEY', type: 'password', placeholder: 'AIza...' },
                { key: 'COLOR_GEMINI_API_BASE', i18n: 'label_COLOR_GEMINI_API_BASE', type: 'text', placeholder: 'https://generativelanguage.googleapis.com' },
                { key: 'COLOR_GEMINI_MODEL', i18n: 'label_COLOR_GEMINI_MODEL', type: 'text', placeholder: 'gemini-2.0-flash-preview-image-generation' },
            ],
        },
        {
            id: 'render-openai',
            category: 'renderer',
            name: 'OpenAI Renderer',
            note: '需单独配置，不会回落到翻译分组。',
            keys: [
                { key: 'RENDER_OPENAI_API_KEY', i18n: 'label_RENDER_OPENAI_API_KEY', type: 'password', placeholder: 'sk-...' },
                { key: 'RENDER_OPENAI_API_BASE', i18n: 'label_RENDER_OPENAI_API_BASE', type: 'text', placeholder: 'https://api.openai.com/v1' },
                { key: 'RENDER_OPENAI_MODEL', i18n: 'label_RENDER_OPENAI_MODEL', type: 'text', placeholder: 'gpt-image-1' },
            ],
        },
        {
            id: 'render-gemini',
            category: 'renderer',
            name: 'Gemini Renderer',
            note: '需单独配置，不会回落到翻译分组。Google Cloud / Vertex 相关 API Key 也直接填在这里；Base URL 保持默认官方地址即可，无需修改。',
            keys: [
                { key: 'RENDER_GEMINI_API_KEY', i18n: 'label_RENDER_GEMINI_API_KEY', type: 'password', placeholder: 'AIza...' },
                { key: 'RENDER_GEMINI_API_BASE', i18n: 'label_RENDER_GEMINI_API_BASE', type: 'text', placeholder: 'https://generativelanguage.googleapis.com' },
                { key: 'RENDER_GEMINI_MODEL', i18n: 'label_RENDER_GEMINI_MODEL', type: 'text', placeholder: 'gemini-2.0-flash-preview-image-generation' },
            ],
        },
    ];

    window.ApiKeySchema = {
        categories,
        groups,
        envKeys: groups.flatMap(group => group.keys.map(item => item.key)),
        getGroupsByCategory(categoryId) {
            return groups.filter(group => group.category === categoryId);
        },
    };
})();
