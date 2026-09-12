import Vue from "vue";
import store from "./index.js";
import i18n from "../../vue/i18n";
import Editor from "../../vue/components/Editor.vue";

export var partVM = new Vue({
    el: "#editor",
    store,
    i18n,
    components: {
        editor: Editor,
    },
});
