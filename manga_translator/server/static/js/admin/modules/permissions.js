// 权限管理模块
class PermissionsModule {
    constructor(app) {
        this.app = app;
    }
    
    async load() {
        await this.loadPermissions();
    }
    
    async loadPermissions() {
        const tbody = document.getElementById('permissions-table-body');
        if (!tbody) return;
        
        // 预定义的权限列表
        const permissions = [
            { id: 'translate', name: '翻译图片', description: '允许使用翻译功能', category: '基础功能' },
            { id: 'batch_translate', name: '批量翻译', description: '允许批量翻译多张图片', category: '基础功能' },
            { id: 'upload', name: '上传文件', description: '允许上传图片文件', category: '基础功能' },
            { id: 'download', name: '下载结果', description: '允许下载翻译结果', category: '基础功能' },
            { id: 'can_save_to_server_result', name: '保存到服务器目录', description: '允许将翻译结果直接写入服务端 result 目录（Docker 映射目录）', category: '基础功能' },
            { id: 'use_api', name: '使用API', description: '允许通过API访问服务', category: 'API' },
            { id: 'manage_api_keys', name: '管理API密钥', description: '允许创建和管理API密钥', category: 'API' },
            { id: 'view_history', name: '查看历史', description: '允许查看翻译历史', category: '历史记录' },
            { id: 'delete_history', name: '删除历史', description: '允许删除翻译历史', category: '历史记录' },
            { id: 'admin_access', name: '管理员访问', description: '允许访问管理控制台', category: '管理' },
            { id: 'manage_users', name: '管理用户', description: '允许管理其他用户', category: '管理' },
            { id: 'manage_groups', name: '管理用户组', description: '允许管理用户组', category: '管理' },
            { id: 'view_logs', name: '查看日志', description: '允许查看系统日志', category: '管理' }
        ];
        
        tbody.innerHTML = permissions.map(p => `
            <tr>
                <td><code style="background:#f3f4f6;padding:2px 6px;border-radius:4px;">${p.id}</code></td>
                <td><strong>${p.name}</strong></td>
                <td>${p.description}</td>
                <td><span class="badge badge-info">${p.category}</span></td>
            </tr>
        `).join('');
    }
}

window.PermissionsModule = PermissionsModule;
