mod gemini;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_persisted_scope::init())
        .invoke_handler(tauri::generate_handler![
            gemini::gemini_key_status,
            gemini::gemini_save_key,
            gemini::gemini_delete_key,
            gemini::gemini_read_page,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Escript AI Desktop");
}
