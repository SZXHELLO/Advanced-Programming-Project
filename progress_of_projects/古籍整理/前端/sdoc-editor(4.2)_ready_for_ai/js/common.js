/* ============================================
   古籍在线编辑器 - 公共脚本
   SDOC Editor - Common JavaScript
   ============================================ */

// ===== 数据存储键名 =====
const STORAGE_KEYS = {
    PROJECT: 'sdoc_project',
    PAGES: 'sdoc_pages',
    ANNOTATIONS: 'sdoc_annotations',
    CUSTOM_CHARS: 'sdoc_custom_chars',
    PROJECT_META: 'sdoc_project_meta'
};

// ===== 属性类型与颜色映射 =====
const ATTR_TYPE_COLORS = {
    '字': '#C84A32',    // --mark-red
    '词': '#2B5F8E',    // --mark-blue
    '句子': '#5B8C5A'    // --mark-green
};

// ===== 属性类型名称 =====
const ATTR_TYPE_NAMES = {
    '字': '字（单字）',
    '词': '词（词语）',
    '句子': '句子'
};

// ===== 全局状态管理 =====
const AppState = {
    project: null,
    pages: [],
    annotations: [],
    customChars: [],
    currentPageIndex: 0,

    // 从localStorage加载数据
    load() {
        try {
            const projectMeta = localStorage.getItem(STORAGE_KEYS.PROJECT_META);
            if (projectMeta) {
                this.project = JSON.parse(projectMeta);
            }

            const pages = localStorage.getItem(STORAGE_KEYS.PAGES);
            if (pages) {
                this.pages = JSON.parse(pages);
            }

            const annotations = localStorage.getItem(STORAGE_KEYS.ANNOTATIONS);
            if (annotations) {
                this.annotations = JSON.parse(annotations);
            }

            const customChars = localStorage.getItem(STORAGE_KEYS.CUSTOM_CHARS);
            if (customChars) {
                this.customChars = JSON.parse(customChars);
            }
        } catch (e) {
            console.error('加载数据失败:', e);
        }
    },

    // 保存数据到localStorage
    save() {
        try {
            if (this.project) {
                localStorage.setItem(STORAGE_KEYS.PROJECT_META, JSON.stringify(this.project));
            }
            localStorage.setItem(STORAGE_KEYS.PAGES, JSON.stringify(this.pages));
            localStorage.setItem(STORAGE_KEYS.ANNOTATIONS, JSON.stringify(this.annotations));
            localStorage.setItem(STORAGE_KEYS.CUSTOM_CHARS, JSON.stringify(this.customChars));
        } catch (e) {
            console.error('保存数据失败:', e);
        }
    },

    // 清空所有数据
    clear() {
        localStorage.removeItem(STORAGE_KEYS.PROJECT);
        localStorage.removeItem(STORAGE_KEYS.PAGES);
        localStorage.removeItem(STORAGE_KEYS.ANNOTATIONS);
        localStorage.removeItem(STORAGE_KEYS.CUSTOM_CHARS);
        localStorage.removeItem(STORAGE_KEYS.PROJECT_META);
        this.project = null;
        this.pages = [];
        this.annotations = [];
        this.customChars = [];
        this.currentPageIndex = 0;
    },

    // 检查是否有数据
    hasData() {
        return this.pages.length > 0;
    }
};

// ===== 页面导航 =====
const Navigation = {
    // 获取当前页面名称
    getCurrentPage() {
        const path = window.location.pathname;
        const filename = path.substring(path.lastIndexOf('/') + 1);
        return filename || 'index.html';
    },

    // 导航到指定页面
    go(page, params = {}) {
        let url = page;
        const queryParams = [];

        for (const [key, value] of Object.entries(params)) {
            queryParams.push(`${key}=${encodeURIComponent(value)}`);
        }

        if (queryParams.length > 0) {
            url += '?' + queryParams.join('&');
        }

        window.location.href = url;
    },

    // 获取URL参数
    getParams() {
        const searchParams = new URLSearchParams(window.location.search);
        const params = {};
        for (const [key, value] of searchParams) {
            params[key] = decodeURIComponent(value);
        }
        return params;
    }
};

// ===== 模态框控制 =====
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
    }
}

// 关闭所有模态框
function closeAllModals() {
    document.querySelectorAll('.modal-overlay.active').forEach(m => {
        m.classList.remove('active');
    });
}

// ESC键关闭模态框
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeAllModals();
    }
});

// 点击遮罩关闭
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});

// ===== 颜色相关 =====
const ColorMap = {
    'var(--mark-red)': '#C84A32',
    'var(--mark-blue)': '#2B5F8E',
    'var(--mark-green)': '#5B8C5A',
    'var(--mark-yellow)': '#E8C547',
    'var(--mark-purple)': '#7B5BA6',
    'var(--mark-orange)': '#D67B3C',
    '#C84A32': '#C84A32',
    '#2B5F8E': '#2B5F8E',
    '#5B8C5A': '#5B8C5A',
    '#E8C547': '#E8C547',
    '#7B5BA6': '#7B5BA6',
    '#D67B3C': '#D67B3C',
    '#E0E0E0': '#E0E0E0'  // 斜线颜色
};

// 获取标注类型的颜色
function getMarkTypeColor(markType) {
    return ColorMap[markType] || markType;
}

// 获取属性类型的颜色
function getAttrTypeColor(attrType) {
    return ATTR_TYPE_COLORS[attrType] || '#C84A32';
}

// ===== 标注类型名称 =====
function getMarkTypeName(type) {
    const names = {
        'box': '框选',
        'underline': '下划线',
        'highlight': '高亮',
        'strikethrough': '删除线',
        'slash': '斜线'
    };
    return names[type] || type;
}

// ===== XML生成器 =====
const XMLGenerator = {
    generate() {
        const proj = AppState.project || {};
        const annotations = AppState.annotations;

        let xml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
        xml += `<article id="${proj.id || 'article_1'}" type="1" version="1.0">\n`;

        // head
        xml += `  <head>\n`;
        if (proj.title) {
            xml += `    <title name="">${this.escapeXml(proj.title)}</title>\n`;
        }
        if (proj.author) {
            xml += `    <authors>\n`;
            xml += `      <author name="${this.escapeXml(proj.author)}" type="0"/>\n`;
            xml += `    </authors>\n`;
        }
        if (proj.book) {
            xml += `    <book name="${this.escapeXml(proj.book)}" volume="${this.escapeXml(proj.volume || '')}" pages="${AppState.pages.length}"/>\n`;
        }
        if (proj.dynasty) {
            xml += `    <date dynasty="${this.escapeXml(proj.dynasty)}"/>\n`;
        }
        xml += `  </head>\n`;

        // content
        xml += `  <content page_mode="1">\n`;
        AppState.pages.forEach((page, i) => {
            const pageAnnotations = annotations.filter(a => a.pageId === page.id && a.type !== 'slash');
            xml += `    <page id="${page.id}" page_no="${i + 1}">\n`;
            xml += `      <panel>\n`;
            pageAnnotations.forEach(ann => {
                // 包含属性类型属性
                const attrTypeAttr = ann.attrType ? ` attrType="${ann.attrType}"` : '';
                xml += `        <textfield id="${ann.id}" simplified="${this.escapeXml(ann.simplifiedChar || '')}" note="${this.escapeXml(ann.note || '')}"${attrTypeAttr}/>\n`;
            });
            xml += `      </panel>\n`;
            xml += `    </page>\n`;
        });
        xml += `  </content>\n`;

        /* // view - 包含斜线标注
        xml += `  <view count="${AppState.pages.length}">\n`;
        AppState.pages.forEach((page, i) => {
            const pageAnnotations = annotations.filter(a => a.pageId === page.id);
            xml += `    <svg id="svg_${page.id}" page_no="${i + 1}" width="${page.width}" height="${page.height}">\n`;
            pageAnnotations.forEach(ann => {
                const colorHex = ann.color;
                switch (ann.type) {
                    case 'box':
                        xml += `      <rect x="${ann.rect.x}" y="${ann.rect.y}" width="${ann.rect.width}" height="${ann.rect.height}" style="fill:none;stroke:${colorHex};stroke-width:2"/>\n`;
                        break;
                    case 'underline':
                        xml += `      <line x1="${ann.rect.x}" y1="${ann.rect.y + ann.rect.height}" x2="${ann.rect.x + ann.rect.width}" y2="${ann.rect.y + ann.rect.height}" style="stroke:${colorHex};stroke-width:3"/>\n`;
                        break;
                    case 'highlight':
                        xml += `      <rect x="${ann.rect.x}" y="${ann.rect.y}" width="${ann.rect.width}" height="${ann.rect.height}" style="fill:${colorHex};fill-opacity:0.3;stroke:none"/>\n`;
                        break;
                    case 'strikethrough':
                        xml += `      <line x1="${ann.rect.x}" y1="${ann.rect.y + ann.rect.height/2}" x2="${ann.rect.x + ann.rect.width}" y2="${ann.rect.y + ann.rect.height/2}" style="stroke:${colorHex};stroke-width:3"/>\n`;
                        break;
                    case 'slash':
                        // 斜线标注
                        xml += `      <line x1="${ann.x1}" y1="${ann.y1}" x2="${ann.x2}" y2="${ann.y2}" style="stroke:${colorHex};stroke-width:1;fill:none"/>\n`;
                        break;
                }
            });
            xml += `    </svg>\n`;
        });
        xml += `  </view>\n`; */

        // sources
        xml += `  <sources>\n`;
        AppState.pages.forEach((page, i) => {
            xml += `    <source src="${this.escapeXml(page.imageSrc)}" pageno="${i + 1}"/>\n`;
        });
        xml += `  </sources>\n`;

        xml += `</article>`;

        return xml;
    },

    escapeXml(str) {
        if (!str) return '';
        return str.replace(/[<>&'"]/g, c => ({
            '<': '&lt;',
            '>': '&gt;',
            '&': '&amp;',
            "'": '&apos;',
            '"': '&quot;'
        }[c]));
    }
};

// ===== 文件下载 =====
function downloadFile(content, filename, mimeType = 'text/plain') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ===== 项目导入导出 =====
const ProjectManager = {
    // 导出项目
    export() {
        const projectData = {
            project: AppState.project,
            pages: AppState.pages,
            annotations: AppState.annotations,
            customChars: AppState.customChars,
            exportedAt: new Date().toISOString()
        };

        const filename = `${AppState.project?.title || '古籍项目'}_${new Date().toISOString().split('T')[0]}.sdocproj`;
        downloadFile(JSON.stringify(projectData, null, 2), filename, 'application/json');
    },

    // 导入项目
    import(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => {
                try {
                    const data = JSON.parse(e.target.result);
                    AppState.project = data.project;
                    AppState.pages = data.pages || [];
                    AppState.annotations = data.annotations || [];
                    AppState.customChars = data.customChars || [];
                    AppState.currentPageIndex = 0;
                    AppState.save();
                    resolve(data);
                } catch (err) {
                    reject(new Error('无法解析项目文件'));
                }
            };
            reader.onerror = () => reject(new Error('读取文件失败'));
            reader.readAsText(file);
        });
    }
};

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', () => {
    // 加载数据
    AppState.load();

    // 设置当前导航链接
    const currentPage = Navigation.getCurrentPage();
    document.querySelectorAll('.nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPage || (currentPage === 'index.html' && href === 'index.html')) {
            link.classList.add('active');
        }
    });
});

// ===== 页面加载完成后执行 =====
window.addEventListener('load', () => {
    document.body.classList.add('loaded');
});
