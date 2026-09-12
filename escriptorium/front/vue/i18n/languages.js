/** UI languages come from Django (`ESC_LANGUAGES` → json_script in base.html). */

const FALLBACK = [{ code: "en", label: "English" }];

export function readUiLanguages(doc = document) {
    const el = doc.getElementById("esc-ui-languages");
    if (!el) return FALLBACK.slice();
    try {
        const parsed = JSON.parse(el.textContent);
        if (!Array.isArray(parsed) || !parsed.length) return FALLBACK.slice();
        const langs = parsed
            .filter((item) => item && item.code)
            .map((item) => ({
                code: String(item.code).split("-")[0],
                label: item.label || String(item.code),
            }));
        return langs.length ? langs : FALLBACK.slice();
    } catch (_err) {
        return FALLBACK.slice();
    }
}

export function supportedLocales(doc = document) {
    return readUiLanguages(doc).map((item) => item.code);
}
