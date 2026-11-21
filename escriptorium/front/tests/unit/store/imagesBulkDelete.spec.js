import images from "../../../vue/store/modules/images";
import { bulkDeleteParts } from "../../../src/api";

jest.mock("../../../src/api", () => ({
    bulkDeleteParts: jest.fn(),
    bulkMoveParts: jest.fn(),
    deleteDocumentPart: jest.fn(),
    moveDocumentPart: jest.fn(),
    retrieveDocument: jest.fn(),
    retrieveDocumentParts: jest.fn(),
    segmentDocument: jest.fn(),
}));

describe("bulk image deletion", () => {
    const run = (parts) => {
        const commit = jest.fn();
        const dispatch = jest.fn().mockResolvedValue();
        return { result: images.actions.deleteSelectedParts({ commit, dispatch, rootState: { document: { id: 4 } }, state: { selectedParts: parts } }), commit, dispatch };
    };

    it("sends 450 images in three sequential chunks and refreshes", async () => {
        bulkDeleteParts.mockImplementation(async ({ parts }) => ({ data: { deleted: parts.length } }));
        const { result, dispatch } = run(Array.from({ length: 450 }, (_, i) => i + 1));
        await result;
        expect(bulkDeleteParts.mock.calls.map(([arg]) => arg.parts.length)).toEqual([200, 200, 50]);
        expect(dispatch).toHaveBeenCalledWith("fetchParts");
    });

    it("refreshes and clears selection when a later chunk fails", async () => {
        bulkDeleteParts.mockReset().mockResolvedValueOnce({ data: { deleted: 200 } }).mockRejectedValueOnce(new Error("failed"));
        const { result, dispatch, commit } = run(Array.from({ length: 450 }, (_, i) => i + 1));
        await result;
        expect(dispatch).toHaveBeenCalledWith("fetchParts");
        expect(commit).toHaveBeenCalledWith("setSelectedParts", []);
        expect(commit).toHaveBeenCalledWith("setDeleteModalOpen", false);
    });
});
