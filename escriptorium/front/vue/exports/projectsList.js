import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import ProjectsList from "../pages/ProjectsList/ProjectsList.vue";

export default new Vue({
    el: "#projects-list",
    store,
    i18n,
    components: {
        "projects-list": ProjectsList,
    },
});
