import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import Images from "../pages/Images/Images.vue";

export default new Vue({
    el: "#images-page",
    store,
    i18n,
    components: {
        "images-page": Images,
    },
});
