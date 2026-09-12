from django.conf import settings
from django.utils.translation import get_language_info


def disable_search(request):
    return {'DISABLE_ELASTICSEARCH': getattr(settings,
                                             'DISABLE_ELASTICSEARCH',
                                             True)}


def enable_cookie_consent(request):
    return {'ENABLE_COOKIE_CONSENT': getattr(settings,
                                             'ENABLE_COOKIE_CONSENT',
                                             True)}


def custom_homepage(request):
    return {'CUSTOM_HOME': getattr(settings, 'CUSTOM_HOME', False)}


def custom_contributors(request):
    return {'CUSTOM_CONTRIBUTORS': getattr(settings, 'CUSTOM_CONTRIBUTORS', False)}


def enable_text_alignment(request):
    return {'TEXT_ALIGNMENT_ENABLED': getattr(settings,
                                              'TEXT_ALIGNMENT_ENABLED',
                                              True)}


def enable_markdown_export(request):
    return {'EXPORT_OPENITI_MARKDOWN_ENABLED': getattr(
        settings, 'EXPORT_OPENITI_MARKDOWN_ENABLED', True,
    )}


def enable_tei_export(request):
    return {'EXPORT_TEI_XML_ENABLED': getattr(settings,
                                              'EXPORT_TEI_XML_ENABLED',
                                              True)}


def models_version_retention(request):
    return {'MODELS_VERSION_RETENTION': getattr(settings,
                                                'MODELS_VERSION_RETENTION')}


def esc_ui_languages(request):
    """Vue reads this via json_script; do not hardcode the list in JS."""
    langs = []
    for code, name in settings.LANGUAGES:
        short = (code or "").split("-")[0]
        try:
            label = get_language_info(short)["name_local"]
        except KeyError:
            label = str(name)
        langs.append({"code": short, "label": label})
    if not langs:
        langs = [{"code": "en", "label": "English"}]
    return {"esc_ui_languages": langs}
