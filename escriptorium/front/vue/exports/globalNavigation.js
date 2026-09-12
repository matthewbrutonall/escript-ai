import Vue from "vue";
import store from "../store";
import i18n from "../i18n";
import GlobalNavigation from "../components/GlobalNavigation/GlobalNavigation.vue";
import "../index.css";

export default new Vue({
    el: "#vue-global-nav",
    store,
    i18n,
    components: {
        "global-navigation": GlobalNavigation,
    },
});
