/**
 * DBCheck RBAC 前端权限守卫
 * 功能: 请求拦截器、Token 自动携带、401 跳转
 */

(function() {
    'use strict';

    // 请求拦截器 — 自动携带 Token
    const originalFetch = window.fetch;
    window.fetch = function(url, options = {}) {
        const token = localStorage.getItem('token');
        if (token) {
            options.headers = options.headers || {};
            options.headers['Authorization'] = 'Bearer ' + token;
        }
        return originalFetch(url, options).then(response => {
            if (response.status === 401) {
                const currentPath = window.location.pathname;
                if (currentPath !== '/um/login') {
                    localStorage.clear();
                    window.location.href = '/um/login';
                }
            }
            return response;
        });
    };

    // 页面加载时检查登录状态
    document.addEventListener('DOMContentLoaded', function() {
        const currentPath = window.location.pathname;

        // 登录页面不需要检查
        if (currentPath === '/um/login' || currentPath.startsWith('/static/')) {
            return;
        }

        const token = localStorage.getItem('token');
        if (!token && currentPath !== '/um/login') {
            // 有 token 才检查，无 token 让后端返回 401 处理
        }
    });

    // 暴露工具函数
    window.DBCheckAuth = {
        getToken: function() {
            return localStorage.getItem('token');
        },
        getUser: function() {
            try {
                return JSON.parse(localStorage.getItem('user') || 'null');
            } catch(e) {
                return null;
            }
        },
        isLoggedIn: function() {
            return !!localStorage.getItem('token');
        },
        logout: function() {
            localStorage.clear();
            window.location.href = '/um/login';
        }
    };

    console.log('[DBCheck RBAC] 权限守卫已加载');
})();
