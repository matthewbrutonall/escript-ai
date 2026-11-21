import VisuLine from "../../../vue/components/VisuLine.vue";

describe("engine line notes", () => {
    const computed = VisuLine.options.computed;
    const context = (note, content = "") => ({
        line: { pk: 8, currentTrans: { content } },
        $store: { state: { lines: { engineNotes: note ? { 8: note } : {} } } },
        get engineNote() { return computed.engineNote.call(this); },
    });

    it("marks an empty engine result with a dashed orange stroke and title text", () => {
        const vm = context({ empty: true, status: "included" });
        expect(vm.engineNote).toEqual({ empty: true, status: "included" });
        expect(computed.maskStrokeColor.call(vm)).toBe("#d9822b");
        expect(computed.engineNoteText.call(vm)).toBe("The recognition engine returned no text for this line.");
    });

    it("hides the mark as soon as transcription text exists", () => {
        const vm = context({ empty: true, status: "included" }, "corrected text");
        expect(vm.engineNote).toBeNull();
        expect(computed.maskStrokeColor.call(vm)).toBe("none");
    });
});
