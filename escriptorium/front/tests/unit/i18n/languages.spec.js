import { readUiLanguages, supportedLocales } from "../../../vue/i18n/languages";

function docWith(json) {
    const doc = document.implementation.createHTMLDocument("");
    const el = doc.createElement("script");
    el.id = "esc-ui-languages";
    el.type = "application/json";
    el.textContent = json;
    doc.body.appendChild(el);
    return doc;
}

describe("readUiLanguages", () => {
    it("falls back to English when the page has no list", () => {
        const doc = document.implementation.createHTMLDocument("");
        expect(readUiLanguages(doc)).toEqual([{ code: "en", label: "English" }]);
    });

    it("reads Django json_script payload", () => {
        const doc = docWith(JSON.stringify([
            { code: "en", label: "English" },
            { code: "hi", label: "हिन्दी" },
        ]));
        expect(supportedLocales(doc)).toEqual(["en", "hi"]);
        expect(readUiLanguages(doc)[1].label).toBe("हिन्दी");
    });

    it("strips regional suffixes", () => {
        const doc = docWith(JSON.stringify([{ code: "pt-br", label: "Português" }]));
        expect(supportedLocales(doc)).toEqual(["pt"]);
    });

    it("ignores junk JSON", () => {
        const doc = docWith("{not json");
        expect(supportedLocales(doc)).toEqual(["en"]);
    });
});
