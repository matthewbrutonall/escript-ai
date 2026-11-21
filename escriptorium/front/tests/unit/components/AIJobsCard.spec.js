import { shallowMount } from "@vue/test-utils";
import AIJobsCard from "../../../vue/components/AIJobsCard/AIJobsCard.vue";
import { retrieveAiJobs } from "../../../src/api/document";

jest.mock("../../../src/api/document", () => ({ retrieveAiJobs: jest.fn() }));

describe("AIJobsCard", () => {
    const mountCard = async (jobs) => {
        retrieveAiJobs.mockResolvedValue({ data: jobs });
        const wrapper = shallowMount(AIJobsCard, { propsData: { documentId: 1 }, mocks: { $t: (key) => key } });
        await wrapper.vm.$nextTick();
        await Promise.resolve();
        await wrapper.vm.$nextTick();
        return wrapper;
    };

    it("renders nothing when there are no jobs", async () => {
        const wrapper = await mountCard([]);
        expect(wrapper.find("section").exists()).toBe(false);
        wrapper.destroy();
    });

    it("shows the safe message and omits empty technical detail", async () => {
        const wrapper = await mountCard([{ pk: 7, mode: "region", status: "error", message: "Fixed public sentence", error: "" }]);
        expect(wrapper.text()).toContain("Fixed public sentence");
        expect(wrapper.text()).not.toContain("Technical detail");
        wrapper.destroy();
    });

    it("shows a partial-failure message on a completed job", async () => {
        const wrapper = await mountCard([{ pk: 8, mode: "seg_review", status: "done", message: "Some pages could not be reviewed." }]);
        expect(wrapper.text()).toContain("Some pages could not be reviewed.");
        wrapper.destroy();
    });
});
