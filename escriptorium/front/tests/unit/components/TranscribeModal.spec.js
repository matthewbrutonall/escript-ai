import TranscribeModal from "../../../vue/components/TranscribeModal/TranscribeModal.vue";
import { estimateAiTranscription } from "../../../src/api/document";

jest.mock("../../../src/api/document", () => ({ estimateAiTranscription: jest.fn() }));

describe("TranscribeModal budget estimate", () => {
    beforeEach(() => estimateAiTranscription.mockReset());

    const context = () => ({
        isAiModel: true,
        model: "ai:1",
        layerName: "AI layer",
        documentId: 9,
        selectedParts: [],
        estimate: null,
        estimateState: "idle",
        estimateSeq: 0,
        refreshEstimate: null,
    });

    it("invalidates submission when the estimate denies the budget", () => {
        const vm = context();
        vm.estimateState = "ready";
        vm.estimate = { allowed: false, reason: "Budget exceeded" };
        expect(TranscribeModal.computed.invalid.call(vm)).toBe(true);
    });

    it("does not allow submission until the estimate succeeds", () => {
        const vm = context();
        expect(TranscribeModal.computed.invalid.call(vm)).toBe(true);
        vm.estimateState = "ready";
        vm.estimate = { allowed: true };
        expect(TranscribeModal.computed.invalid.call(vm)).toBe(false);
    });

    it("refreshes the estimate when the selected pages change", async () => {
        estimateAiTranscription.mockResolvedValue({ data: { allowed: true } });
        const vm = context();
        vm.refreshEstimate = TranscribeModal.methods.refreshEstimate.bind(vm);
        vm.selectedParts = [11, 12];
        await TranscribeModal.watch.selectedParts.call(vm);
        expect(estimateAiTranscription).toHaveBeenCalledWith({
            documentId: 9, backend: "1", parts: [11, 12],
        });
    });

    it("ignores a late estimate response for an earlier model", async () => {
        let resolveFirst;
        let resolveSecond;
        estimateAiTranscription
            .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
            .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));
        const vm = context();
        const first = TranscribeModal.methods.refreshEstimate.call(vm);
        vm.model = "ai:2";
        const second = TranscribeModal.methods.refreshEstimate.call(vm);
        resolveSecond({ data: { allowed: false, reason: "Second model denied" } });
        await second;
        resolveFirst({ data: { allowed: true, reason: "Old response" } });
        await first;
        expect(vm.estimate.reason).toBe("Second model denied");
        expect(vm.estimateState).toBe("ready");
        expect(estimateAiTranscription).toHaveBeenNthCalledWith(2, {
            documentId: 9, backend: "2", parts: [],
        });
    });
});
