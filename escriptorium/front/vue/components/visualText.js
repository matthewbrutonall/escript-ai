/** Sizing helpers for the visual (facsimile) transcription panel. */

export const MIN_VISUAL_FONT_PX = 14;
export const MAX_VISUAL_FONT_PX = 72;
export const LINE_FONT_FILL = 0.78;
export const SHRINK_OVERFLOW_RATIO = 1.02;

/**
 * Old default was 0.25, then compounded with another * 0.3, which made
 * manuscript lines unreadable. Treat leftover tiny stored values as "never
 * adjusted" and start at 1.
 */
export function normalizeFontSizeRatio(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return 1;
    if (n < 0.5) return 1;
    return n;
}

export function displayedLineHeight(mask, ratio, fallback = 30) {
    const scale = Number(ratio) || 1;
    if (!mask || !mask.length) return fallback;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const pt of mask) {
        const y = pt[1];
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }
    const height = Math.abs((maxY - minY) * scale);
    return height >= 1 ? height : fallback;
}

export function visualFontSize(displayedHeight, fontSizeRatio) {
    const ratio = normalizeFontSizeRatio(fontSizeRatio);
    const raw = displayedHeight * LINE_FONT_FILL * ratio;
    return Math.min(MAX_VISUAL_FONT_PX, Math.max(MIN_VISUAL_FONT_PX, raw));
}

/** Stretch short strings to the baseline; only shrink when they overflow. */
export function shouldShrinkToPath(naturalLength, pathLength) {
    if (!naturalLength || !pathLength) return false;
    return naturalLength > pathLength * SHRINK_OVERFLOW_RATIO;
}
