import React, { useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'

interface Props {
    /** 推理 / Observation / 工具过程等纯文本 */
    text: string
    /** 标题，默认「推理与工具过程」 */
    title?: string
    defaultOpen?: boolean
}

/**
 * 类似 DeepSeek「已思考」区的折叠面板：承载 ReAct 循环中的 Observation、工具输出等，
 * 与最终对用户的气泡回复分离。
 */
export const ThinkingCollapsible: React.FC<Props> = ({
    text,
    title = '推理与工具过程',
    defaultOpen = true,
}) => {
    const [open, setOpen] = useState(defaultOpen)

    if (!text.trim()) {
        return null
    }

    return (
        <div className="mb-4 overflow-hidden rounded-xl border border-indigo-100 bg-indigo-50/90 shadow-sm">
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-indigo-950 hover:bg-indigo-100/60 transition-colors"
            >
                <Brain className="shrink-0 text-indigo-600" size={18} aria-hidden />
                <span>{title}</span>
                <ChevronDown
                    className={`ml-auto shrink-0 text-indigo-600 transition-transform ${open ? 'rotate-180' : ''}`}
                    size={18}
                    aria-hidden
                />
            </button>
            {open && (
                <pre
                    className="max-h-[min(24rem,50vh)] overflow-auto border-t border-indigo-100/80 bg-white/60 px-4 py-3 text-xs leading-relaxed text-slate-800 whitespace-pre-wrap font-mono"
                    tabIndex={0}
                >
                    {text.trimEnd()}
                </pre>
            )}
        </div>
    )
}
