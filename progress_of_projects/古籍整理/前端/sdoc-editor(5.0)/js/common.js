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
        let isTitleOpen = false;
        AppState.pages.forEach((page, i) => {
            const pageAnnotations = annotations
                .filter(a => a.pageId === page.id && a.type !== 'slash')
                .map(a => this.normalizeAnnotationRelations(a));
            const byId = new Map(pageAnnotations.map(a => [a.id, a]));
            const rootChars = pageAnnotations.filter(a => a.attrType === '字' && (!a.parentId || byId.get(a.parentId)?.attrType === '标题'));
            const rootWords = pageAnnotations.filter(a => a.attrType === '词' && (!a.parentId || byId.get(a.parentId)?.attrType === '标题'));
            const sentences = pageAnnotations.filter(a => a.attrType === '句子');

            // 标题包裹逻辑：当某一页存在“标题”标注时，从该页起包裹后续 page
            // 遇到下一次“标题”时先闭合再开启新的 <title>
            const titleAnn = pageAnnotations.find(a => a.attrType === '标题' && !a.parentId);
            if (titleAnn) {
                if (isTitleOpen) {
                    xml += `    </title>\n`;
                }
                const titleName = (titleAnn.simplifiedChar || '').trim();
                const titleNote = (titleAnn.note || '').trim();
                xml += `    <title name="${this.escapeXml(titleName)}" note="${this.escapeXml(titleNote)}">\n`;
                isTitleOpen = true;
            }

            xml += `    <page id="${page.id}" page_no="${i + 1}">\n`;
            xml += `      <panel>\n`;
            xml += `        <singleCharacter>\n`;
            rootChars.forEach(ann => {
                xml += this.renderTextfield(ann, 10);
            });
            xml += `        </singleCharacter>\n`;
            xml += `        <singleWord>\n`;
            rootWords.forEach(ann => {
                xml += this.renderTextfield(ann, 10);
            });
            xml += `        </singleWord>\n`;
            xml += `        <sentence>\n`;
            sentences.forEach(sentence => {
                const children = (sentence.childIds || [])
                    .map(childId => byId.get(childId))
                    .filter(Boolean);
                xml += this.renderTextfield(sentence, 10, children);
            });
            xml += `        <sentence>\n`;
            xml += `      </panel>\n`;
            xml += `    </page>\n`;
        });
        if (isTitleOpen) {
            xml += `    </title>\n`;
        }
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

    normalizeAnnotationRelations(ann) {
        const normalized = { ...ann };
        if (normalized.parentId === undefined) normalized.parentId = null;
        if (!Array.isArray(normalized.childIds)) normalized.childIds = [];
        return normalized;
    },

    renderTextfield(ann, indentSpaces = 0, children = []) {
        const indent = ' '.repeat(indentSpaces);
        const attrTypeAttr = ann.attrType ? ` attrType="${this.escapeXml(ann.attrType)}"` : '';
        const base = `${indent}<textfield id="${this.escapeXml(ann.id || '')}" simplified="${this.escapeXml(ann.simplifiedChar || '')}" note="${this.escapeXml(ann.note || '')}"${attrTypeAttr}`;

        if (!children.length) {
            return `${base}/>\n`;
        }

        let xml = `${base}>\n`;
        children.forEach(child => {
            xml += this.renderTextfield(this.normalizeAnnotationRelations(child), indentSpaces + 2);
        });
        xml += `${indent}</textfield>\n`;
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



// -------------------------- AI识别文字核心函数 --------------------------
async function aiRecognizeText() {
    try {
        // 1. 获取当前页的图片和页码
        const currentPage = getCurrentPage();
        const pageImage = await getPageImage(currentPage);
        if (!pageImage) {
            updateStatus("未找到当前页图片", "error");
            return;
        }

        // 2. 构造请求，调用后端接口
        const formData = new FormData();
        formData.append("image", pageImage, `page_${currentPage}.png`);
        formData.append("page", currentPage);

        updateStatus("正在识别文字...", "loading");
        const response = await fetch("http://localhost:8000/api/recognize-text", {
            method: "POST",
            body: formData,
        });

        if (!response.ok) throw new Error("识别请求失败");
        const result = await response.json();

        // 3. 处理识别结果，自动创建并保存为标准标注数据
        const createdCount = processTextResult(result);
        updateStatus(`成功识别${createdCount}个文字`, "success");

    } catch (err) {
        console.error("AI识别失败：", err);
        updateStatus(`识别失败：${err.message}`, "error");
    }
}

// -------------------------- 核心函数：AI划分图文区域 --------------------------
async function aiSegmentRegions() {
    try {
        const currentPage = getCurrentPage();
        const pageImage = await getPageImage(currentPage);
        if (!pageImage) {
            updateStatus("未找到当前页图片", "error");
            return;
        }

        const formData = new FormData();
        formData.append("image", pageImage);

        updateStatus("正在划分区域...", "loading");
        const response = await fetch("http://localhost:8000/api/segment-regions", {
            method: "POST",
            body: formData,
        });

        if (!response.ok) throw new Error("区域划分请求失败");
        const result = await response.json();

        // 渲染区域划分结果到Canvas
        drawRegions(result.regions);
        updateStatus(`成功划分${result.regions.length}个区域`, "success");

    } catch (err) {
        console.error("区域划分失败：", err);
        updateStatus(`划分失败：${err.message}`, "error");
    }
}

// -------------------------- 辅助函数：处理文字识别结果（自动创建标注） --------------------------
function processTextResult(result) {
    const page = AppState.pages[AppState.currentPageIndex];
    if (!page || !Array.isArray(result?.words) || result.words.length === 0) {
        if (typeof updateAnnotationList === "function") updateAnnotationList();
        if (typeof updateStatusBar === "function") updateStatusBar();
        return 0;
    }

    const selectedColor = document.querySelector("#colorPicker .color-swatch.selected")?.dataset.color || "#C84A32";
    const selectedAttrType = document.querySelector("#attrTypeContainer .attr-type-btn.active")?.dataset.type || "字";

    if (typeof saveUndoState === "function") {
        saveUndoState();
    }

    const newAnnotations = [];
    for (const word of result.words) {
        const x = Number(word?.x);
        const y = Number(word?.y);
        const width = Number(word?.width);
        const height = Number(word?.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
            continue;
        }
        if (width <= 0 || height <= 0) {
            continue;
        }

        const annotation = {
            id: "ann_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8),
            pageId: page.id,
            type: "box",
            rect: { x, y, width, height },
            color: selectedColor,
            simplifiedChar: word?.text || "",
            note: "",
            attrType: selectedAttrType
        };
        newAnnotations.push(annotation);
    }

    if (newAnnotations.length === 0) {
        if (typeof updateAnnotationList === "function") updateAnnotationList();
        if (typeof updateStatusBar === "function") updateStatusBar();
        return 0;
    }

    AppState.annotations.push(...newAnnotations);

    if (typeof redrawAnnotations === "function") redrawAnnotations();
    if (typeof updateAnnotationList === "function") updateAnnotationList();
    if (typeof updateStatusBar === "function") updateStatusBar();

    if (typeof selectAnnotationById === "function") {
        selectAnnotationById(newAnnotations[0].id);
    }

    if (typeof markAsModified === "function") {
        markAsModified();
    } else {
        AppState.save();
    }

    return newAnnotations.length;
}

// -------------------------- 辅助函数：创建标注卡片（自动填充文字） --------------------------
function createAnnotationCard(annotation) {
    const card = document.createElement("div");
    card.className = "annotation-card";
    card.dataset.id = annotation.id;
    card.innerHTML = `
        <div class="annotation-preview">
            <div class="annotation-char">${annotation.text}</div>
            <div class="annotation-info">
                <div class="annotation-simplified">${annotation.text}</div>
                <div class="annotation-type">${annotation.attrType} | ${annotation.type}</div>
            </div>
            <div class="annotation-actions">
                <button class="annotation-action-btn" onclick="editAnnotation('${annotation.id}')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                    </svg>
                </button>
                <button class="annotation-action-btn delete" onclick="deleteAnnotation('${annotation.id}')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                </button>
            </div>
        </div>
    `;

    // 点击卡片：自动填充右侧属性输入框
    card.addEventListener("click", () => {
        document.querySelectorAll(".annotation-card").forEach(c => c.classList.remove("selected"));
        card.classList.add("selected");
        // 填充简体字输入框
        document.getElementById("simplifiedCharInput").value = annotation.text;
        // 选中默认颜色和属性类型
        selectColor(document.querySelector(`.color-swatch[data-color="${annotation.color}"]`));
        selectAttrType(document.querySelector(`.attr-type-btn[data-type="${annotation.attrType}"]`));
    });

    return card;
}

// -------------------------- 辅助函数：绘制区域划分结果到Canvas --------------------------
function drawRegions(regions) {
    const annoLayer = document.getElementById("annotationLayer");
    const ctx = annoLayer.getContext("2d");
    const canvasWrapper = document.getElementById("canvasWrapper");
    // 适配Canvas缩放比例（关键：AI返回的是原始图片坐标，需同步Canvas缩放）
    const scale = parseFloat(document.getElementById("zoomValue").textContent) / 100;

    // 清空原有绘制
    ctx.clearRect(0, 0, annoLayer.width, annoLayer.height);
    annoLayer.width = canvasWrapper.offsetWidth;
    annoLayer.height = canvasWrapper.offsetHeight;

    // 绘制每个区域
    regions.forEach(region => {
        const x = region.x * scale;
        const y = region.y * scale;
        const w = region.width * scale;
        const h = region.height * scale;

        // 区分文字/图片区样式
        const color = region.type === "text" ? "#2B5F8E" : "#5B8C5A";
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y, w, h);

        // 绘制区域标签
        ctx.fillStyle = color;
        ctx.font = "12px Noto Sans SC";
        ctx.fillText(region.type === "text" ? "文字区" : "图片区", x + 5, y + 15);
    });
}

// -------------------------- 基础辅助函数 --------------------------
// 获取当前页码
function getCurrentPage() {
    const indicator = document.getElementById("pageIndicator");
    return parseInt(indicator.textContent.split(" / ")[0]);
}

// 获取当前页图片（转为Blob）
async function getPageImage(page) {
    const thumbnailItems = document.querySelectorAll(".thumbnail-item");
    const activeItem = Array.from(thumbnailItems).find(item =>
        item.querySelector(".thumbnail-page").textContent == page
    );
    if (!activeItem) return null;

    const imgUrl = activeItem.querySelector("img").src;
    const response = await fetch(imgUrl);
    return await response.blob();
}

// 更新底部状态栏（复用现有逻辑）
function updateStatus(text, type) {
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    statusText.textContent = text;
    // 设置状态点颜色：loading(蓝色)/success(绿色)/error(红色)
    statusDot.className = `status-dot ${type}`;
}

// 绘制单个标注到Canvas（需适配现有drawAnnotation逻辑）
function drawAnnotationToCanvas(annotation) {
    const annoLayer = document.getElementById("annotationLayer");
    const ctx = annoLayer.getContext("2d");
    const scale = parseFloat(document.getElementById("zoomValue").textContent) / 100;

    const x = annotation.x * scale;
    const y = annotation.y * scale;
    const w = annotation.width * scale;
    const h = annotation.height * scale;

    ctx.strokeStyle = annotation.color;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
}
