import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import ModelTraining from "../pages/ModelTraining/ModelTraining.vue";

export default new Vue({
    el: "#model-training",
    store,
    i18n,
    components: {
        "model-training": ModelTraining,
    },
});
