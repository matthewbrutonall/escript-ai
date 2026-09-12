import {
    displayedLineHeight,
    normalizeFontSizeRatio,
    shouldShrinkToPath,
    visualFontSize,
    MIN_VISUAL_FONT_PX,
} from "../../../vue/components/visualText";

describe("visualText", () => {
    describe("normalizeFontSizeRatio", () => {
        it("treats the old 0.25 default as 1", () => {
            expect(normalizeFontSizeRatio(0.25)).toBe(1);
        });
        it("keeps a user-enlarged ratio", () => {
            expect(normalizeFontSizeRatio(1.4)).toBe(1.4);
        });
        it("falls back for junk", () => {
            expect(normalizeFontSizeRatio(0)).toBe(1);
            expect(normalizeFontSizeRatio(null)).toBe(1);
        });
    });

    describe("displayedLineHeight", () => {
        it("scales mask bbox by the panel ratio", () => {
            const mask = [[0, 10], [100, 10], [100, 50], [0, 50]];
            expect(displayedLineHeight(mask, 0.5)).toBe(20);
        });
        it("falls back without a mask", () => {
            expect(displayedLineHeight(null, 1)).toBe(30);
        });
    });

    describe("visualFontSize", () => {
        it("floors tiny manuscript lines", () => {
            expect(visualFontSize(8, 1)).toBe(MIN_VISUAL_FONT_PX);
        });
        it("fills most of a displayed line", () => {
            expect(visualFontSize(40, 1)).toBeCloseTo(31.2);
        });
    });

    describe("shouldShrinkToPath", () => {
        it("does not stretch short titles", () => {
            expect(shouldShrinkToPath(80, 400)).toBe(false);
        });
        it("shrinks overflow", () => {
            expect(shouldShrinkToPath(420, 400)).toBe(true);
        });
    });
});
