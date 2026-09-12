import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import Project from "../pages/Project/Project.vue";

export default new Vue({
    el: "#project-dashboard",
    store,
    i18n,
    components: {
        "project-dashboard": Project,
    },
});
