import alerts from "../../../vue/store/modules/alerts";

describe("alerts/addError DRF validation", () => {
    const invoke = async (response) => {
        const commit = jest.fn();
        await alerts.actions.addError({ commit }, { response, message: "failed" });
        return commit.mock.calls.filter(([type]) => type === "addAlert").map(([, value]) => value);
    };

    it("keeps DRF field errors until dismissed", async () => {
        expect(await invoke({ status: 400, data: { transcription: ["x"] } })).toEqual([
            { color: "alert", message: "Error: x", delay: 0 },
        ]);
    });

    it("preserves the legacy error response", async () => {
        expect(await invoke({ status: 400, data: { error: "y" } })).toEqual([
            { color: "alert", message: "Error: y", delay: 0 },
        ]);
    });

    it("keeps the generic non-400 path", async () => {
        expect(await invoke({ status: 500, data: {} })).toEqual([
            { color: "alert", message: "failed", delay: undefined },
        ]);
    });
});
