from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_modals_use_explicit_close_state_instead_of_native_dialogs() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "<dialog" not in html
    assert "data-close-modal" in html
    assert ".showModal(" not in javascript
    assert "confirm(" not in javascript


def test_collection_management_includes_edit_and_delete_actions() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'id="delete-collection"' in html
    assert 'data-action="manage-collection"' in javascript
    assert "method: 'DELETE'" in javascript


def test_settings_and_multi_plate_query_controls_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'id="download-path"' in html
    assert 'id="proxy-address"' in html
    assert 'id="chapter-concurrency"' not in html
    assert 'id="image-concurrency"' not in html
    assert 'data-action="add-query-item"' in javascript
    assert "metadata.items" in javascript
    assert "这部作品已经在书架中" in javascript
    assert "补齐到书架" in javascript
    assert 'id="sanity-mode"' in html
    assert 'id="detail-cover-privacy-start"' in html
    assert 'data-action="choose-comic-cover"' in javascript
    assert "coverPrivacyMask" in javascript


def test_preview_loading_uses_staged_motion_and_four_page_status() -> None:
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert "reader-loading-visual" in javascript
    assert "reader-loading-steps" in javascript
    assert "每四页一组送达" in javascript
    assert "reader-stream-state" in stylesheet
    assert "reader-sheet-front" in stylesheet


def test_application_update_controls_and_endpoints_are_wired() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'id="app-update-check"' in html
    assert 'id="app-update-download"' in html
    assert 'id="app-update-apply"' in html
    assert 'id="app-update-new"' in html
    assert 'id="app-update-dialog"' in html
    assert 'id="app-update-badge"' not in html
    assert 'id="about-dialog"' in html
    assert 'id="open-account"' in html
    assert 'id="open-settings"' in html
    assert "checkAppUpdate" in javascript
    assert "downloadAppUpdate" in javascript
    assert "applyAppUpdate" in javascript
    assert "'/api/app/update/check'" in javascript
    assert "'/api/app/update/download'" in javascript
    assert "'/api/app/update/apply'" in javascript
    assert "'/api/app/about'" in javascript
    assert "runStartupNetworkTasks" in javascript
    assert 'id="app-update-current"' in html
    assert 'id="app-update-target"' in html
    assert "app-update-summary" in html


def test_jm_favorites_and_startup_library_update_ui_are_wired() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'data-collection="jm-favorites"' in html
    assert html.index('data-collection="jm-favorites"') < html.index('data-collection="unfiled"')
    assert 'id="jm-favorites-count"' in html
    assert 'data-action="download-favorite"' in html
    assert 'data-action="add-favorite-to-shelf"' in html
    assert "addFavoriteToShelf(comic)" in javascript
    assert "/api/provider/favorites/${encodeURIComponent(albumId)}/shelf" in javascript
    assert 'id="library-update-dialog"' in html
    assert "'/api/library-series/startup-check'" in javascript
    assert "/api/provider/favorites?" in javascript
    assert "remote-favorite-card" in javascript
    assert "showStartupLibraryUpdates" in javascript
    assert ".remote-favorite-card .card-cover img" in stylesheet
    assert ".library-update-item" in stylesheet
    assert "library-update-cover" in javascript
    assert ".library-update-cover" in stylesheet


def test_jmonline_search_recommendations_and_library_sort_are_wired() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'data-collection="jm-online"' in html
    assert 'id="online-search-form"' in html
    assert 'id="online-time"' in html
    assert 'value="custom"' in html
    assert 'id="library-sort"' in html
    assert 'value="most-read"' in html
    assert 'value="unread"' in html
    assert "/api/online/search?" in javascript
    assert "/api/online/recommendations?${params}" in javascript
    assert "metadataActive < 5" in javascript
    assert "albumIds: [sourceId], priority" in javascript
    assert "IntersectionObserver" in javascript
    assert "rootMargin: '900px 0px'" in javascript
    assert "scheduleOnlinePreviewPreloads" not in javascript
    assert "preloadOnlinePreviewHead" not in javascript
    assert "body: { chapterId }" in javascript
    assert "setActivityFocus(isOnline ? 'online' : 'library')" in javascript
    assert 'id="online-clear-temp-cache"' in html
    assert "'/api/online/cache'" in javascript
    assert "data-cover-fallback" in javascript
    assert "fetchpriority=\"high\"" in javascript
    assert "/api/comics/${comic.id}/read" in javascript
    assert "sortLibraryItems" in javascript
    assert ".online-search-panel" in stylesheet
    assert ".online-card-actions" in stylesheet


def test_recommendation_tag_exclusions_are_searchable_persisted_and_case_insensitive() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'id="online-exclude-tags-toggle"' in html
    assert 'id="online-exclude-tag-query"' in html
    assert "ONLINE_RECOMMEND_EXCLUDED_TAGS_KEY" in javascript
    assert "'/api/online/preferences'" in javascript
    assert "toLocaleLowerCase()" in javascript
    assert "params.append('excludeTag', tag)" in javascript
    assert ".online-exclude-tags-panel" in stylesheet


def test_reader_modes_icons_and_library_series_controls_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'data-reader-mode="scroll"' in html
    assert 'data-reader-mode="page"' in html
    assert 'data-reader-mode="book"' in html
    assert 'id="quick-add-ball"' in html
    assert 'id="add-series"' not in html
    assert 'href="/ukp.png"' in html
    assert (ROOT / "public" / "ukp.png").is_file()
    assert 'class="card-fallback">冊' not in javascript
    assert "state.readerMode === 'book'" in javascript
    assert "data-action=\"show-series-comic-detail\"" in javascript
    assert 'id="reader-auto-mode"' in html
    assert '<option value="continuous">连续匀速</option>' in html
    assert '<option value="stepped">整屏分段</option>' in html
    assert 'id="reader-auto-toggle"' in html
    assert 'id="reader-auto-settings"' in html
    assert 'id="reader-auto-popover"' in html
    assert "runContinuousReaderAutoScroll" in javascript
    assert "scheduleSteppedReaderAutoScroll" in javascript
    assert "state.readerAutoPosition + READER_AUTO_SPEEDS" in javascript
    assert "reader.clientHeight - 28" in javascript
    assert 'id="reader-book-direction"' in html
    assert "jmshelf-reader-book-left-forward" in javascript
    assert "state.readerBookLeftForward === leftPage" in javascript
    assert (ROOT / "public" / "icons" / "pause.svg").is_file()


def test_native_multi_p_series_has_direct_chapter_layout_and_one_book_count() -> None:
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert "source-series-card" in javascript
    assert "JM MULTI-P" in javascript
    assert 'data-action="read-source-series-comic"' in javascript
    assert "query-source-series" in javascript
    assert "items.slice(0, 4)" in javascript
    assert "$('#all-count').textContent = state.activeCollection ? '—' : allItemCount" in javascript
    assert ".series-book-list.is-source-series" in stylesheet
    assert "禁漫书库" in javascript
    assert "沿用第一 P 封面" not in javascript
    assert "原生章节" not in javascript
    assert "source-series-progress" in javascript
    assert "showSeriesContextMenu" in javascript
    assert "查看详情" in javascript
    assert "toggleSeriesCollection" in javascript
    assert "showSmartCollectionContextMenu" in javascript
    assert "在资源管理器中显示" in javascript
    assert "is-source-series-detail" in javascript
    assert "series-latest-info" in javascript
    assert ".series-view-card.is-source-series" in stylesheet
    assert "height: 540px" in stylesheet
    assert "height: 205px; max-height: 205px" in stylesheet
    assert ".detail-drawer.is-source-series-detail" in stylesheet
    assert "height: 0; flex: 1 1 0" in stylesheet
    assert ".source-series-progress .icon" in stylesheet


def test_library_type_quick_filters_are_wired() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'id="library-filter"' in html
    assert '>ALL</button>' in html
    assert 'data-library-filter="custom"' in html
    assert 'data-library-filter="source"' in html
    assert 'data-library-filter="standalone"' in html
    assert "series.kind !== 'source' && !series.isSourceSeries" in javascript
    assert "series.kind === 'source' || series.isSourceSeries" in javascript
    assert "jmshelf-library-filter" in javascript
    assert ".library-filter {" in stylesheet
    assert ".library-filter-button.filter-custom" in stylesheet
    assert ".library-filter-button.filter-source" in stylesheet


def test_context_menus_status_copy_and_prominent_delete_controls_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'id="context-menu"' in html
    assert "showComicContextMenu" in javascript
    assert "showCollectionContextMenu" in javascript
    assert "加入待读" in javascript
    assert "已连接 JM" in javascript
    assert "仅监听 127.0.0.1" not in html
    assert 'class="button danger" data-action="remove-comic"' in html
    assert 'id="confirm-alternative"' in html
    assert "choice === 'alternative'" in javascript
    assert 'class="button danger hidden" id="delete-collection"' in html
    assert "缓存以供阅读" in javascript
    assert "清除阅读缓存" in javascript
    assert "清除系列缓存" in javascript
    assert "'/api/caches'" in javascript
    assert "'/api/caches/clear'" in javascript
    assert "cacheComics(comics, { notify: false })" in javascript
    assert "remoteFavorite || !state.settings.sanityMode" in javascript
    assert 'id="detail-cover-privacy-settings"' in html


def test_download_queue_and_collection_drag_drop_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert 'id="download-queue-panel"' in html
    assert 'id="download-queue-badge"' in html
    assert "'/api/downloads'" in javascript
    assert 'draggable="true"' in javascript
    assert "addDraggedItemsToCollection" in javascript
    assert "application/x-jmshelf-library-item" in javascript
    assert 'data-action="retry-download"' in javascript
    assert "list.children[index]" in javascript


def test_account_login_and_cross_chapter_reader_controls_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'id="account-username"' in html
    assert 'id="account-password"' in html
    assert 'id="reader-chapter-prev"' in html
    assert 'id="reader-chapter-next"' in html
    assert 'id="reader-directory"' in html
    assert "preloadNextReaderChapter" in javascript
    assert "switchReaderChapter" in javascript
    assert 'id="series-continue-reading"' in html
    assert "seriesResumeComic" in javascript
    assert "series-progress-note" in javascript
    assert 'id="series-refresh"' in html
    assert 'id="series-chapter-scroll"' in html
    assert 'class="series-scroll-region series-builder-content"' in html
    assert "refreshSeries" in javascript
    assert "updateAvailableCount" in javascript
    assert "classList.add('reader-open')" in javascript
    assert "body.reader-open .floating-actions" in stylesheet
    assert "event.button !== 1" in javascript
    assert "event.shiftKey" in javascript


def test_native_window_shell_controls_are_present() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "public" / "styles.css").read_text(encoding="utf-8")
    assert 'class="window-controls native-only"' in html
    assert 'id="window-minimize"' in html
    assert 'id="window-maximize"' in html
    assert 'id="window-close"' in html
    assert 'id="window-close" class="window-close"' in html
    assert 'icon icon-window-close' in html
    assert ".icon-close { --icon-source: url('/icons/cross.svg'); }" in stylesheet
    assert ".icon-window-close { --icon-source: url('/icons/close.svg'); }" in stylesheet
    assert (ROOT / "public" / "icons" / "cross.svg").is_file()
    assert "pywebviewready" in javascript
    assert "nativeWindowAction('toggle-maximize')" in javascript
    assert 'id="window-drag-region"' not in html
    assert "classList.toggle('pywebview-drag-region'" not in javascript
    assert "data-native-drag-region" in html
    assert 'class="reader-toolbar" data-native-drag-region' in html
    assert "begin_drag" in javascript
    assert "is-window-transforming-out" in javascript
    assert "is-window-transforming-in" in javascript
    assert "body.is-window-transforming-out::after" in stylesheet
    assert "window-cover-in" in stylesheet
    assert "window-transform-out" not in stylesheet
    assert "data-native-resize" in html
    assert "begin_resize" in javascript


def test_frontend_asset_versions_are_kept_in_sync() -> None:
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    import re

    stylesheet_version = re.search(r'/styles\.css\?v=([^"\']+)', html)
    javascript_version = re.search(r'/app\.js\?v=([^"\']+)', html)
    assert stylesheet_version is not None
    assert javascript_version is not None
    assert stylesheet_version.group(1) == javascript_version.group(1)
