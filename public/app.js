const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  comics: [],
  collections: [],
  provider: null,
  jmFavorites: null,
  jmFavoritesLoading: false,
  settings: {},
  series: [],
  activeCollection: '',
  libraryFilter: 'all',
  librarySort: 'updated',
  librarySearch: '',
  online: {
    view: 'home',
    results: [],
    recommendations: [],
    response: null,
    recommendationSeed: null,
    recommendationProfile: null,
    recommendationReason: '',
    recommendationBatch: 0,
    recommendationBatchCount: 1,
    recommendationSort: 'diverse',
    excludedRecommendationTags: [],
    recommendationExcludedCount: 0,
    recommendationFilterRevision: 0,
    recommendationLoading: false,
    recommendationUpdatedAt: '',
    recommendationRefreshing: false,
    recommendationPollTimer: null,
    homeSections: [],
    homeUpdatedAt: '',
    homeLoading: false,
    homeLoaded: false,
    homeReason: '',
    homePollTimer: null,
    browseCategory: null,
    metadataLoading: new Set(),
    metadataQueued: new Set(),
    metadataQueue: [],
    metadataActive: 0,
    metadataObserver: null,
    loading: false,
    recommendationLoaded: false,
    hasSearched: false,
    recentSearches: [],
  },
  activeComic: null,
  activeSeries: null,
  seriesSelection: [],
  seriesCandidateComics: [],
  queryMetadata: null,
  editingCollectionId: null,
  confirmResolve: null,
  readerComic: null,
  readerSeries: null,
  readerPages: [],
  readerPageIndex: 0,
  readerMode: 'scroll',
  readerObserver: null,
  readerProgressTimer: null,
  readerStreamTimer: null,
  readerLoadingTimer: null,
  readerTurnTimer: null,
  readerAutoMode: 'continuous',
  readerAutoPace: 'normal',
  readerAutoRunning: false,
  readerAutoFrame: null,
  readerAutoTimer: null,
  readerAutoLastFrame: 0,
  readerAutoPosition: null,
  readerAutoSettingsOpen: false,
  readerBookLeftForward: false,
  readerPrefetchLinks: [],
  readerPreview: false,
  readerPreviewAlbumId: '',
  readerPendingComic: null,
  readerRequestToken: 0,
  readerReturnDetailId: null,
  searchTimer: null,
  contextActions: [],
  downloads: [],
  downloadStateReady: false,
  downloadPollBusy: false,
  caches: [],
  cacheStateReady: false,
  cachePollBusy: false,
  appUpdate: { state: 'idle', currentVersion: '—', available: false, progress: 0 },
  about: null,
  appUpdatePoll: null,
  startupLibraryUpdateShown: false,
  dragPayload: null,
  suppressCardClickUntil: 0
};

const modalCloseTimers = new Map();
const THEME_STORAGE_KEY = 'jmshelf-theme';
const ONLINE_SEARCH_HISTORY_KEY = 'jmshelf-online-search-history';
const ONLINE_RECOMMEND_SORT_KEY = 'jmshelf-online-recommend-sort';
const ONLINE_RECOMMEND_EXCLUDED_TAGS_KEY = 'jmshelf-online-recommend-excluded-tags';
const ONLINE_CATEGORY_LABELS = {
  all: '全部', doujin: '同人', single: '单本', short: '短篇', hanman: '韩漫', meiman: '美漫',
  cosplay: 'Cosplay', '3d': '3D', another: '其他', english: '英文站'
};
const READER_AUTO_SPEEDS = { slow: 24, normal: 42, fast: 68 };
const READER_AUTO_PAUSES = { slow: 7000, normal: 5000, fast: 3000 };
let nativeWindowWasMinimized = false;
let nativeWindowRestoreTimer = null;
let nativeWindowTransformTimer = null;
let nativeWindowTransforming = false;
let panicHideInFlight = false;

function applyTheme(theme, { persist = true } = {}) {
  const selected = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selected;
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, selected); } catch (_) {}
  }
  const toggle = $('#theme-toggle');
  if (toggle) {
    const nextThemeLabel = selected === 'dark' ? '亮色' : '暗色';
    toggle.setAttribute('aria-label', `切换到${nextThemeLabel}主题`);
    toggle.setAttribute('title', `切换到${nextThemeLabel}主题`);
  }
  const themeColor = $('meta[name="theme-color"]');
  if (themeColor) themeColor.content = selected === 'light' ? '#f4e8c8' : '#101116';
  try { window.AndroidBridge?.setTheme?.(selected); } catch (_) {}
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
}

function icon(name, className = '') {
  return `<span class="icon icon-${name}${className ? ` ${className}` : ''}" aria-hidden="true"></span>`;
}

function coverPrivacyMask(comic, forcePreview = false) {
  if ((!state.settings.sanityMode && !forcePreview) || !comic?.coverPrivacyEnabled) return '';
  const direction = comic.coverPrivacyDirection === 'above' ? 'above' : 'below';
  const start = Math.max(15, Math.min(85, Number(comic.coverPrivacyStart || 58)));
  return `<span class="cover-privacy-mask is-${direction}" style="--privacy-start:${start}%" aria-hidden="true"></span>`;
}

function comicCoverUrl(comic) {
  return `/media/cover/${comic.id}?v=${encodeURIComponent(comic.updatedAt || '')}`;
}

function isRemoteComic(comic) {
  return Boolean(comic?.remoteFavorite || comic?.remoteOnline);
}

function renderDetailCover(comic = state.activeComic) {
  if (!comic) return;
  const remoteComic = isRemoteComic(comic);
  const coverUrl = remoteComic ? comic.coverUrl : (comic.coverPath ? comicCoverUrl(comic) : '');
  $('#detail-cover').innerHTML = `${coverUrl ? `<img src="${coverUrl}" alt="封面">` : '<div class="cover-placeholder"><img src="/ukp.png" alt=""></div>'}
    ${coverPrivacyMask(comic, true)}
    <button type="button" class="detail-cover-picker ${remoteComic ? 'hidden' : ''}" data-action="choose-comic-cover" title="选择本地图片作为封面">${icon('edit')}<span>选择封面</span></button>`;
}

function updateDetailPrivacyPreview() {
  if (!state.activeComic) return;
  const preview = {
    ...state.activeComic,
    coverPrivacyEnabled: $('#detail-cover-privacy-enabled').checked,
    coverPrivacyDirection: $('#detail-cover-privacy-direction').value === 'above' ? 'above' : 'below',
    coverPrivacyStart: Number($('#detail-cover-privacy-start').value)
  };
  $('#detail-cover-privacy-value').textContent = `${preview.coverPrivacyStart}%`;
  renderDetailCover(preview);
}

function setNativeMaximized(maximized) {
  const target = $('#window-maximize-icon');
  if (!target) return;
  document.body.classList.toggle('is-window-maximized', Boolean(maximized));
  target.classList.toggle('icon-window-restore', Boolean(maximized));
  target.classList.toggle('icon-window-maximize', !maximized);
  const button = $('#window-maximize');
  button?.setAttribute('aria-label', maximized ? '还原窗口' : '最大化窗口');
  button?.setAttribute('title', maximized ? '还原窗口' : '最大化窗口');
}

function finishNativeWindowTransform() {
  document.body.classList.remove('is-window-transforming-out');
  void document.body.offsetWidth;
  document.body.classList.add('is-window-transforming-in');
  clearTimeout(nativeWindowTransformTimer);
  nativeWindowTransformTimer = setTimeout(() => {
    document.body.classList.remove('is-window-transforming-in');
    nativeWindowTransforming = false;
  }, 240);
}

async function nativeWindowAction(action) {
  const isTransform = action === 'toggle-maximize';
  if (isTransform && nativeWindowTransforming) return;
  const button = $({ minimize: '#window-minimize', 'toggle-maximize': '#window-maximize', close: '#window-close' }[action]);
  button?.classList.remove('is-window-action');
  void button?.offsetWidth;
  button?.classList.add('is-window-action');
  if (action === 'minimize') {
    nativeWindowWasMinimized = true;
    document.body.classList.add('is-window-minimizing');
    await new Promise((resolve) => setTimeout(resolve, 125));
  }
  if (isTransform) {
    nativeWindowTransforming = true;
    clearTimeout(nativeWindowTransformTimer);
    document.body.classList.remove('is-window-transforming-in');
    document.body.classList.add('is-window-transforming-out');
    await new Promise((resolve) => setTimeout(resolve, 105));
  }
  if (window.pywebview?.api?.window_action) {
    try {
      const result = await window.pywebview.api.window_action(action);
      if (typeof result?.maximized === 'boolean') setNativeMaximized(result.maximized);
      return;
    } finally {
      setTimeout(() => button?.classList.remove('is-window-action'), 360);
      if (isTransform) finishNativeWindowTransform();
    }
  }
  if (action === 'minimize') await api('/api/window/minimize', { method: 'POST' });
  if (action === 'close') {
    await api('/api/app/exit', { method: 'POST' }).catch(() => {});
    window.close();
  }
  if (isTransform) finishNativeWindowTransform();
  setTimeout(() => button?.classList.remove('is-window-action'), 360);
}

async function activateNativeShell() {
  document.body.classList.add('native-window');
  try {
    const state = await window.pywebview.api.window_state();
    setNativeMaximized(state.maximized);
  } catch (_) { setNativeMaximized(false); }
}

if (new URLSearchParams(window.location.search).get('desktop') === '1') {
  document.body.classList.add('native-window');
}
window.addEventListener('pywebviewready', activateNativeShell);

window.addEventListener('focus', () => {
  if (!nativeWindowWasMinimized) return;
  nativeWindowWasMinimized = false;
  document.body.classList.remove('is-window-minimizing');
  document.body.classList.remove('is-window-restoring');
  void document.body.offsetWidth;
  document.body.classList.add('is-window-restoring');
  clearTimeout(nativeWindowRestoreTimer);
  nativeWindowRestoreTimer = setTimeout(() => document.body.classList.remove('is-window-restoring'), 260);
});

$$('.topbar .search-box, .topbar .window-controls').forEach((control) => {
  control.addEventListener('mousedown', (event) => event.stopPropagation());
});

$$('[data-native-drag-region]').forEach((region) => {
  region.addEventListener('mousedown', (event) => {
    if (event.button !== 0 || event.target.closest('button, input, select, textarea, a, .search-box, .window-controls')) return;
    event.preventDefault();
    event.stopPropagation();
    if (document.body.classList.contains('is-window-maximized')) return;
    window.pywebview?.api?.begin_drag?.();
  });
});

$$('[data-native-resize]').forEach((handle) => {
  handle.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || document.body.classList.contains('is-window-maximized')) return;
    const bridge = window.pywebview?.api;
    if (!bridge?.begin_resize) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const ready = bridge.begin_resize(handle.dataset.nativeResize, event.screenX, event.screenY, window.devicePixelRatio || 1);
    const session = { latest: null, busy: false, finished: false };

    const pump = async () => {
      if (session.busy || !session.latest || session.finished) return;
      session.busy = true;
      const point = session.latest;
      session.latest = null;
      try {
        await ready;
        await bridge.resize_window(point.x, point.y);
      } finally {
        session.busy = false;
        if (session.latest) pump();
      }
    };
    const move = (moveEvent) => {
      session.latest = { x: moveEvent.screenX, y: moveEvent.screenY };
      pump();
    };
    const finish = async () => {
      session.finished = true;
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', finish);
      handle.removeEventListener('pointercancel', finish);
      await ready.catch(() => {});
      await bridge.end_resize?.().catch(() => {});
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  });
});

$('.topbar').addEventListener('dblclick', (event) => {
  if (event.target.closest('.search-box, .window-controls')) return;
  nativeWindowAction('toggle-maximize');
});

async function api(url, options = {}) {
  const config = { ...options, headers: { 'X-JmShelf-Request': '1', ...(options.headers || {}) } };
  if (config.body && typeof config.body !== 'string') {
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(url, config);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function setActivityFocus(scope) {
  api('/api/activity/focus', { method: 'POST', body: { scope } }).catch(() => {});
}

function cancelOnlinePreview(albumId) {
  const sourceId = String(albumId || '');
  if (!sourceId) return;
  api(`/api/online/${encodeURIComponent(sourceId)}/preview/cancel`, { method: 'POST' }).catch(() => {});
}

async function clearOnlineTempCache() {
  const button = $('#online-clear-temp-cache');
  setBusy(button, true, '清理中…');
  try {
    state.online.metadataQueue = [];
    state.online.metadataQueued.clear();
    state.online.metadataObserver?.disconnect();
    state.online.metadataObserver = null;
    const result = await api('/api/online/cache', { method: 'DELETE' });
    toast(`已清理 ${Number(result.metadataRows || 0)} 条索引、${Number(result.thumbnails || 0)} 张缩略图和 ${Number(result.previewFolders || 0)} 个预览缓存`);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

function toast(message, type = 'info', duration = 3200) {
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  item.textContent = message;
  $('#toast-stack').append(item);
  setTimeout(() => item.classList.add('is-leaving'), Math.max(0, duration - 220));
  setTimeout(() => item.remove(), duration);
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  clearTimeout(modalCloseTimers.get(id));
  modalCloseTimers.delete(id);
  modal.classList.remove('is-closing');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
}

function closeModal(id, result = false) {
  const modal = document.getElementById(id);
  if (!modal || modal.classList.contains('hidden') || modal.classList.contains('is-closing')) return;
  const finish = () => {
    modal.classList.add('hidden');
    modal.classList.remove('is-closing');
    modal.setAttribute('aria-hidden', 'true');
    modalCloseTimers.delete(id);
    if (id === 'confirm-dialog' && state.confirmResolve) {
      const resolve = state.confirmResolve;
      state.confirmResolve = null;
      resolve(result);
    }
    if (!$('.modal:not(.hidden)')) document.body.classList.remove('modal-open');
  };
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return finish();
  modal.classList.add('is-closing');
  modalCloseTimers.set(id, setTimeout(finish, 170));
}

function requestConfirmation({ title, message, confirmLabel = '确认', confirmTone = 'danger', alternativeLabel = '', wide = false }) {
  if (state.confirmResolve) {
    const previousResolve = state.confirmResolve;
    state.confirmResolve = null;
    previousResolve(false);
  }
  $('#confirm-title').textContent = title;
  $('#confirm-message').textContent = message;
  const accept = $('#confirm-accept');
  const alternative = $('#confirm-alternative');
  $('.confirm-card').classList.toggle('is-wide', wide);
  accept.querySelector('span:last-child').textContent = confirmLabel;
  accept.classList.toggle('danger', confirmTone === 'danger');
  accept.classList.toggle('primary', confirmTone === 'primary');
  alternative.classList.toggle('hidden', !alternativeLabel);
  alternative.querySelector('span:last-child').textContent = alternativeLabel || '删除全部';
  openModal('confirm-dialog');
  setTimeout(() => accept.focus(), 30);
  return new Promise((resolve) => { state.confirmResolve = resolve; });
}

function setBusy(button, busy, label = '处理中…') {
  if (!button) return;
  if (busy) {
    button.dataset.oldHtml = button.innerHTML;
    button.textContent = label;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.oldHtml || button.innerHTML;
    delete button.dataset.oldHtml;
    button.disabled = false;
  }
}

function currentCollectionName() {
  if (!state.activeCollection) return '全部本子';
  if (state.activeCollection === 'jm-online') return 'JMonline';
  if (state.activeCollection === 'jm-favorites') return 'JM 收藏';
  if (state.activeCollection === 'unfiled') return '未分类';
  return state.collections.find((item) => item.id === state.activeCollection)?.name || '收藏夹';
}

function remoteFavoriteComic(item) {
  return {
    id: `jm-favorite:${item.albumId}`,
    sourceId: item.albumId,
    favoriteAlbumId: item.albumId,
    title: item.title || `JM${item.albumId}`,
    nickname: '',
    displayName: item.title || `JM${item.albumId}`,
    authors: item.authors || [],
    tags: item.tags || [],
    works: item.works || [],
    actors: item.actors || [],
    description: item.description || '',
    coverPath: null,
    coverUrl: item.coverUrl || `/media/source-cover/${item.albumId}`,
    directCoverUrl: item.directCoverUrl || `https://cdn-msp.jmapiproxy1.cc/media/albums/${item.albumId}_3x4.jpg`,
    coverPrivacyEnabled: false,
    pageCount: Number(item.pageCount || 0),
    progressPage: 0,
    rootPath: null,
    sourceStatus: 'remote-favorite',
    collections: [],
    remoteFavorite: true,
    updatedAt: state.jmFavorites?.fetchedAt || ''
  };
}

function remoteOnlineComic(item) {
  const initialAuthors = item.authors || [];
  const initialTags = item.tags || [];
  return {
    id: `jm-online:${item.albumId}`,
    sourceId: item.albumId,
    favoriteAlbumId: item.albumId,
    title: item.title || `JM${item.albumId}`,
    nickname: '',
    displayName: item.title || `JM${item.albumId}`,
    authors: initialAuthors,
    tags: initialTags,
    works: item.works || [],
    actors: item.actors || [],
    description: item.description || '',
    publishedAt: item.publishedAt || '',
    updatedAtSource: item.updatedAtSource || '',
    coverPath: null,
    coverUrl: item.coverUrl || `/media/source-cover/${item.albumId}`,
    directCoverUrl: item.directCoverUrl || `https://cdn-msp.jmapiproxy1.cc/media/albums/${item.albumId}_3x4.jpg`,
    coverPrivacyEnabled: false,
    pageCount: Number(item.pageCount || 0),
    progressPage: 0,
    rootPath: null,
    sourceStatus: 'remote-online',
    collections: [],
    remoteOnline: true,
    inShelf: Boolean(item.inShelf),
    cached: Boolean(item.cached),
    saved: Boolean(item.saved),
    fullyAvailable: Boolean(item.fullyAvailable),
    episodeManifest: item.episodeManifest || [],
    episodeCount: Number(item.episodeCount || item.episodeManifest?.length || 0),
    firstPhotoId: String(item.firstPhotoId || ''),
    latestSourceId: String(item.latestSourceId || item.episodeManifest?.at?.(-1)?.sourceId || ''),
    isSourceSeries: Boolean(item.isSourceSeries || (item.episodeManifest?.length > 1)),
    recommendationReason: item.recommendationReason || '',
    recommendationScore: Number(item.recommendationScore || 0),
    metadataLoaded: Boolean(item.metadataLoaded),
    detailLoaded: Boolean(item.metadataLoaded || (initialAuthors.length && initialTags.length)),
    updatedAt: item.updatedAtSource || item.publishedAt || '',
  };
}

async function loadJmFavorites({ refresh = false, render = true } = {}) {
  if (!state.provider?.authenticated) {
    state.jmFavorites = null;
    $('#jm-favorites-count').textContent = '—';
    if (render && state.activeCollection === 'jm-favorites') {
      state.comics = [];
      renderLibrary();
    }
    return null;
  }
  if (state.jmFavoritesLoading) return state.jmFavorites;
  state.jmFavoritesLoading = true;
  try {
    const params = new URLSearchParams();
    const search = $('#search-input').value.trim();
    if (search) params.set('search', search);
    if (refresh) params.set('refresh', 'true');
    const favorites = await api(`/api/provider/favorites?${params}`);
    state.jmFavorites = favorites;
    $('#jm-favorites-count').textContent = favorites.total ?? favorites.items?.length ?? 0;
    if (render && state.activeCollection === 'jm-favorites') {
      state.comics = [
        ...(favorites.localComics || []),
        ...(favorites.remoteItems || []).map(remoteFavoriteComic),
      ];
      renderLibrary();
    }
    return favorites;
  } finally {
    state.jmFavoritesLoading = false;
  }
}

async function loadLibrary() {
  if (state.activeCollection === 'jm-online') return;
  if (state.activeCollection === 'jm-favorites') {
    await loadJmFavorites();
    return;
  }
  const params = new URLSearchParams();
  const search = $('#search-input').value.trim();
  if (search) params.set('search', search);
  if (state.activeCollection) params.set('collection', state.activeCollection);
  state.comics = await api(`/api/comics?${params}`);
  renderLibrary();
}

function onlineTagButtons(tags, limit = 3) {
  return (tags || []).slice(0, limit).map((tag) =>
    `<button type="button" data-action="online-search-tag" data-online-query="${escapeHtml(tag)}" title="搜索标签 ${escapeHtml(tag)}">${escapeHtml(tag)}</button>`
  ).join('');
}

function onlineCardTemplate(comic, index = 0) {
  const author = comic.authors.join(' · ') || '未知作者';
  const dateLabel = comic.isSourceSeries
    ? (comic.updatedAtSource || comic.publishedAt || '日期读取中…')
    : (comic.publishedAt || comic.updatedAtSource || '日期读取中…');
  const tags = onlineTagButtons(comic.tags);
  const shelfAction = comic.inShelf
    ? `<button type="button" class="online-card-button is-saved" disabled>${icon('tick')}已在书架</button>`
    : `<button type="button" class="online-card-button" data-action="online-add" data-online-id="${comic.sourceId}">${icon('plus')}加入书架</button>`;
  const priorityCover = index < 8;
  const coverSource = comic.directCoverUrl || comic.coverUrl;
  const coverFallback = comic.directCoverUrl && comic.coverUrl !== comic.directCoverUrl
    ? ` data-cover-fallback="${escapeHtml(comic.coverUrl)}"`
    : '';
  const sourceMark = comic.isSourceSeries
    ? `禁漫书库 · ${comic.episodeCount || comic.episodeManifest.length} 话`
    : `JM${comic.sourceId}`;
  const storageMark = comic.saved
    ? `<span class="online-cache-mark is-saved">${icon('save')}已保存</span>`
    : (comic.cached
      ? `<span class="online-cache-mark">${icon('file')}已缓存</span>`
      : (comic.inShelf ? `<span class="online-cache-mark is-shelf">${icon('tick')}已入架</span>` : ''));
  return `<article class="library-card online-card" data-online-id="${comic.sourceId}" data-action="online-detail" tabindex="0" style="--reveal-index:${index}" title="查看 JM${comic.sourceId} 详情">
    <div class="card-cover online-card-cover">
      <div class="card-fallback"></div>
      <img src="${escapeHtml(coverSource)}"${coverFallback} alt="${escapeHtml(comic.displayName)} 的封面" loading="${priorityCover ? 'eager' : 'lazy'}" decoding="async"${priorityCover ? ' fetchpriority="high"' : ''}>
      <span class="online-comic-mark">${sourceMark}</span>
      ${storageMark}
      <div class="online-card-actions">
        ${shelfAction}
        <button type="button" class="online-card-button" data-action="online-download" data-online-id="${comic.sourceId}">${icon('download')}下载</button>
      </div>
    </div>
    <h3 class="card-title" title="${escapeHtml(comic.displayName)}">${escapeHtml(comic.displayName)}</h3>
    <div class="card-meta"><span>${escapeHtml(author)}</span><span class="online-date">${escapeHtml(dateLabel)}</span></div>
    <div class="online-card-tags ${tags ? '' : 'is-loading'}" data-online-tags="${comic.sourceId}">${tags || '<span>标签读取中…</span>'}</div>
  </article>`;
}

function bindOnlineImages(root) {
  $$('img', root).forEach((image) => {
    if (image.complete && image.naturalWidth) image.classList.add('loaded');
    image.addEventListener('load', () => image.classList.add('loaded'), { once: true });
    image.addEventListener('error', () => {
      const fallback = image.dataset.coverFallback;
      if (fallback) {
        delete image.dataset.coverFallback;
        image.src = fallback;
        return;
      }
      image.remove();
    });
  });
}

function onlineItems() {
  return [
    ...state.online.results,
    ...state.online.recommendations,
    ...state.online.homeSections.flatMap((section) => section.items || []),
  ];
}

function formatSnapshotTime(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function recommendationTagKey(value) {
  return String(value || '').trim().toLocaleLowerCase();
}

function uniqueRecommendationTags(values) {
  const unique = new Map();
  for (const value of values || []) {
    const label = String(value || '').trim();
    const key = recommendationTagKey(label);
    if (key && !unique.has(key)) unique.set(key, label);
  }
  return [...unique.values()];
}

function recommendationTagUniverse() {
  return uniqueRecommendationTags([
    ...(state.online.recommendationProfile?.topTags || []).map((item) => item.name),
    ...state.comics.flatMap((comic) => comic.tags || []),
    ...onlineItems().flatMap((comic) => comic.tags || []),
  ]).sort((left, right) => left.localeCompare(right, 'zh-CN', { sensitivity: 'base' }));
}

async function persistExcludedRecommendationTags() {
  try {
    localStorage.setItem(ONLINE_RECOMMEND_EXCLUDED_TAGS_KEY, JSON.stringify(state.online.excludedRecommendationTags));
  } catch (_) {}
  await api('/api/online/preferences', {
    method: 'PATCH',
    body: { excludedRecommendationTags: state.online.excludedRecommendationTags },
  });
}

function renderRecommendationTagFilter() {
  const tags = state.online.excludedRecommendationTags;
  const selectedKeys = new Set(tags.map(recommendationTagKey));
  const count = $('#online-exclude-tags-count');
  count.textContent = String(tags.length);
  count.classList.toggle('hidden', tags.length === 0);
  $('#online-exclude-tags-toggle').classList.toggle('is-active', tags.length > 0);
  $('#online-clear-excluded-tags').classList.toggle('hidden', tags.length === 0);
  $('#online-excluded-tag-list').innerHTML = tags.length
    ? tags.map((tag) => `<button type="button" data-remove-excluded-tag="${escapeHtml(tag)}" title="取消排除 ${escapeHtml(tag)}"><span>${escapeHtml(tag)}</span><b aria-hidden="true">×</b></button>`).join('')
    : '<small>尚未排除标签</small>';

  const query = $('#online-exclude-tag-query').value.trim();
  const queryKey = recommendationTagKey(query);
  const known = recommendationTagUniverse().filter((tag) => (
    !selectedKeys.has(recommendationTagKey(tag))
    && (!queryKey || recommendationTagKey(tag).includes(queryKey))
  )).slice(0, 16);
  const exactKnown = known.some((tag) => recommendationTagKey(tag) === queryKey);
  const direct = query && !selectedKeys.has(queryKey) && !exactKnown ? [query] : [];
  const suggestions = uniqueRecommendationTags([...direct, ...known]);
  $('#online-exclude-tag-results').innerHTML = suggestions.length
    ? suggestions.map((tag, index) => `<button type="button" data-add-excluded-tag="${escapeHtml(tag)}"${index === 0 ? ' data-first-excluded-result="true"' : ''}><span>${escapeHtml(tag)}</span><small>排除</small></button>`).join('')
    : `<small>${query ? '没有其他匹配标签' : '暂无可选标签，可输入名称后按回车添加'}</small>`;
}

async function setExcludedRecommendationTags(tags) {
  state.online.excludedRecommendationTags = uniqueRecommendationTags(tags);
  state.online.recommendationBatch = 0;
  state.online.recommendationFilterRevision += 1;
  renderRecommendationTagFilter();
  try { await persistExcludedRecommendationTags(); }
  catch (error) { toast(`偏好保存失败：${error.message}`, 'error'); }
  loadOnlineRecommendations({ poll: true }).catch((error) => toast(error.message, 'error'));
}

function addExcludedRecommendationTag(tag) {
  const label = String(tag || '').trim();
  if (!label) return;
  const key = recommendationTagKey(label);
  if (state.online.excludedRecommendationTags.some((item) => recommendationTagKey(item) === key)) return;
  setExcludedRecommendationTags([...state.online.excludedRecommendationTags, label]);
  $('#online-exclude-tag-query').value = '';
  renderRecommendationTagFilter();
}

function removeExcludedRecommendationTag(tag) {
  const key = recommendationTagKey(tag);
  setExcludedRecommendationTags(state.online.excludedRecommendationTags.filter((item) => recommendationTagKey(item) !== key));
}

function needsOnlineMetadata(comic) {
  return Boolean(comic?.sourceId
    && !comic.metadataLoaded
    && (!comic.publishedAt || !comic.tags?.length || !comic.authors?.length || !comic.episodeManifest?.length));
}

async function hydrateOnlineMetadataOne(sourceId, priority) {
  try {
    const response = await api('/api/online/metadata', {
      method: 'POST',
      body: { albumIds: [sourceId], priority },
    });
    for (const metadata of response.items || []) {
      const resolvedSourceId = String(metadata.albumId || sourceId);
      for (const comic of onlineItems()) {
        if (String(comic.sourceId) !== resolvedSourceId) continue;
        comic.publishedAt = metadata.publishedAt || comic.publishedAt || '';
        comic.updatedAtSource = metadata.updatedAtSource || comic.updatedAtSource || '';
        comic.updatedAt = comic.updatedAtSource || comic.publishedAt || comic.updatedAt;
        comic.pageCount = Number(metadata.pageCount || comic.pageCount || 0);
        if (metadata.authors?.length) comic.authors = metadata.authors;
        if (metadata.tags?.length) comic.tags = metadata.tags;
        if (metadata.works?.length) comic.works = metadata.works;
        if (metadata.actors?.length) comic.actors = metadata.actors;
        if (metadata.description) comic.description = metadata.description;
        comic.episodeManifest = metadata.episodeManifest || comic.episodeManifest || [];
        comic.episodeCount = comic.episodeManifest.length;
        comic.firstPhotoId = String(metadata.firstPhotoId || comic.episodeManifest[0]?.sourceId || comic.firstPhotoId || '');
        comic.latestSourceId = String(comic.episodeManifest.at(-1)?.sourceId || comic.latestSourceId || '');
        comic.isSourceSeries = comic.episodeManifest.length > 1;
        comic.metadataLoaded = true;
        comic.detailLoaded = true;
      }
      const isSourceSeries = (metadata.episodeManifest || []).length > 1;
      const dateLabel = isSourceSeries
        ? (metadata.updatedAtSource || metadata.publishedAt || '日期暂不可用')
        : (metadata.publishedAt || metadata.updatedAtSource || '日期暂不可用');
      $$(`[data-online-id="${resolvedSourceId}"] .online-date`).forEach((node) => { node.textContent = dateLabel; });
      $$(`[data-online-id="${resolvedSourceId}"] .online-comic-mark`).forEach((node) => {
        node.textContent = isSourceSeries ? `禁漫书库 · ${metadata.episodeManifest.length} 话` : `JM${resolvedSourceId}`;
      });
      const tagButtons = onlineTagButtons(metadata.tags || []);
      $$(`[data-online-tags="${resolvedSourceId}"]`).forEach((node) => {
        node.innerHTML = tagButtons || '<span>暂无标签</span>';
        node.classList.remove('is-loading');
      });
      if (state.activeComic?.remoteOnline && String(state.activeComic.sourceId) === resolvedSourceId) {
        renderDetailMetadata(state.activeComic);
        configureOnlineDetailActions(state.activeComic);
      }
    }
  } catch (_) {
    $$(`[data-online-id="${sourceId}"] .online-date`).forEach((node) => { node.textContent = '日期暂不可用'; });
    $$(`[data-online-tags="${sourceId}"]`).forEach((node) => {
      node.innerHTML = '<span>标签暂不可用</span>';
      node.classList.remove('is-loading');
    });
  }
}

function pumpOnlineMetadataQueue() {
  while (state.online.metadataActive < 5 && state.online.metadataQueue.length) {
    const task = state.online.metadataQueue.shift();
    state.online.metadataQueued.delete(task.sourceId);
    if (!onlineItems().some((comic) => String(comic.sourceId) === task.sourceId && needsOnlineMetadata(comic))) continue;
    state.online.metadataActive += 1;
    state.online.metadataLoading.add(task.sourceId);
    hydrateOnlineMetadataOne(task.sourceId, task.priority).finally(() => {
      state.online.metadataLoading.delete(task.sourceId);
      state.online.metadataActive = Math.max(0, state.online.metadataActive - 1);
      pumpOnlineMetadataQueue();
    });
  }
}

function queueOnlineMetadata(sourceId, priority = false) {
  const id = String(sourceId || '');
  if (!id || state.online.metadataLoading.has(id) || state.online.metadataQueued.has(id)) return;
  state.online.metadataQueued.add(id);
  const task = { sourceId: id, priority: Boolean(priority) };
  if (priority) {
    const insertAt = state.online.metadataQueue.findIndex((item) => !item.priority);
    state.online.metadataQueue.splice(insertAt < 0 ? state.online.metadataQueue.length : insertAt, 0, task);
  } else {
    state.online.metadataQueue.push(task);
  }
  pumpOnlineMetadataQueue();
}

function onlineMetadataObserver() {
  if (!state.online.metadataObserver && 'IntersectionObserver' in window) {
    state.online.metadataObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        queueOnlineMetadata(entry.target.dataset.onlineId, true);
      });
    }, { rootMargin: '900px 0px' });
  }
  return state.online.metadataObserver;
}

function hydrateOnlineMetadata(items, { eagerCount = 10 } = {}) {
  const sourceIds = [...new Set((items || []).filter(needsOnlineMetadata).map((comic) => String(comic.sourceId)))];
  sourceIds.slice(0, eagerCount).forEach((sourceId) => queueOnlineMetadata(sourceId, true));
  const observer = onlineMetadataObserver();
  sourceIds.slice(eagerCount).forEach((sourceId) => {
    const cards = $$(`.online-card[data-online-id="${sourceId}"]`);
    if (observer && cards.length) cards.forEach((card) => observer.observe(card));
    else queueOnlineMetadata(sourceId, false);
  });
}

function renderOnlineRecommendations() {
  const items = state.online.recommendations;
  const profile = state.online.recommendationProfile;
  $('#online-recommend-grid').innerHTML = items.map((comic, index) => onlineCardTemplate(comic, index)).join('');
  const updatedLabel = formatSnapshotTime(state.online.recommendationUpdatedAt);
  $('#online-recommendation-seed').textContent = profile
    ? `${updatedLabel ? `${updatedLabel} · ` : ''}${state.online.recommendationBatch + 1}/${state.online.recommendationBatchCount}`
    : '正在加载…';
  $('#online-taste-profile').classList.toggle('hidden', !profile);
  const profileSignals = [
    ...(profile?.topTags || []).slice(0, 10).map((item) => ({ ...item, mode: 'tag', kind: '标签' })),
  ];
  $('#online-profile-tags').innerHTML = profileSignals.map((item) =>
    `<button type="button" data-profile-tag="${escapeHtml(item.name)}" data-profile-mode="${item.mode}" title="搜索 ${escapeHtml(item.name)}">#${escapeHtml(item.name)}</button>`
  ).join('');
  const status = $('#online-recommend-status');
  status.classList.toggle('hidden', items.length > 0);
  status.textContent = state.online.recommendationLoading
    ? '正在加载推荐…'
    : (profile
      ? (state.online.excludedRecommendationTags.length ? '没有符合当前标签过滤的推荐' : '暂时没有更多推荐')
      : '暂无推荐');
  $('#online-refresh-recommendations').disabled = state.online.recommendationLoading;
  $('#online-recommendation-sort').disabled = state.online.recommendationLoading;
  bindOnlineImages($('#online-recommend-grid'));
  renderRecommendationTagFilter();
  hydrateOnlineMetadata(items);
  renderOnlineSearchAssists();
}

function replayAnimation(element, className) {
  if (!element || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}

function setOnlineView(view, { focus = false, animate = true } = {}) {
  const selected = ['search', 'categories'].includes(view) ? view : 'home';
  state.online.view = selected;
  const visiblePanels = [];
  $$('[data-online-panel]').forEach((panel) => {
    const visible = panel.dataset.onlinePanel.split(/\s+/).includes(selected);
    panel.classList.toggle('hidden', !visible);
    panel.classList.remove('is-online-panel-entering');
    if (visible) visiblePanels.push(panel);
  });
  if (animate && state.activeCollection === 'jm-online') {
    requestAnimationFrame(() => visiblePanels.forEach((panel) => panel.classList.add('is-online-panel-entering')));
  }
  if (selected === 'search') {
    renderOnlineSearchAssists();
    if (focus) requestAnimationFrame(() => $('#online-query').focus());
  }
  renderQuickAction();
}

function renderOnlineSearchAssists() {
  const profile = state.online.recommendationProfile;
  const suggestions = [
    ...(profile?.topTags || []).slice(0, 10).map((item) => ({ ...item, mode: 'tag', kind: '标签' })),
    ...(profile?.topAuthors || []).slice(0, 6).map((item) => ({ ...item, mode: 'author', kind: '作者' })),
  ];
  $('#online-search-suggestions').innerHTML = suggestions.length
    ? suggestions.map((item) => `<button type="button" data-online-suggestion="${escapeHtml(item.name)}" data-online-suggestion-mode="${item.mode}" title="搜索 ${escapeHtml(item.name)}">${item.mode === 'author' ? '@' : '#'}${escapeHtml(item.name)}</button>`).join('')
    : '';
  $('#online-suggestion-group').classList.toggle('hidden', !suggestions.length);
  $('#online-recent-searches').innerHTML = state.online.recentSearches.length
    ? state.online.recentSearches.map((item) => `<button type="button" data-online-recent="${escapeHtml(item.query)}" data-online-recent-mode="${escapeHtml(item.mode)}">${escapeHtml(item.query)}</button>`).join('')
    : '';
  $('#online-recent-search-group').classList.toggle('hidden', !state.online.recentSearches.length);
  $('#online-clear-history').classList.toggle('hidden', !state.online.recentSearches.length);
}

function rememberOnlineSearch(query, mode) {
  const clean = String(query || '').trim();
  if (!clean) return;
  state.online.recentSearches = [
    { query: clean, mode: String(mode || 'site') },
    ...state.online.recentSearches.filter((item) => item.query.toLocaleLowerCase() !== clean.toLocaleLowerCase()),
  ].slice(0, 8);
  try { localStorage.setItem(ONLINE_SEARCH_HISTORY_KEY, JSON.stringify(state.online.recentSearches)); } catch (_) {}
  renderOnlineSearchAssists();
}

function startOnlineSearch(query, mode = 'site') {
  $('#online-query').value = query;
  $('#search-input').value = query;
  $('#online-mode').value = [...$('#online-mode').options].some((option) => option.value === mode) ? mode : 'site';
  state.online.browseCategory = null;
  setOnlineView('search');
  searchOnline(1);
}

function renderOnlineResults() {
  const response = state.online.response;
  const items = state.online.results;
  $('#online-grid').innerHTML = items.map((comic, index) => onlineCardTemplate(comic, index)).join('');
  bindOnlineImages($('#online-grid'));
  $('#online-empty').classList.toggle('hidden', !state.online.hasSearched || items.length > 0 || state.online.loading);
  if (!response) return;
  $('#online-results-title').textContent = response.resultKind === 'category'
    ? `${ONLINE_CATEGORY_LABELS[response.category] || '分类'}作品`
    : `“${response.query}” 的结果`;
  const pageCount = Math.max(1, Number(response.pageCount || 1));
  const page = Math.max(1, Number(response.page || 1));
  $('#online-result-meta').textContent = `${Number(response.total || 0).toLocaleString('zh-CN')} 项 · 第 ${page}/${pageCount} 页`;
  $('#online-page-label').textContent = `第 ${page} / ${pageCount} 页`;
  $('#online-prev').disabled = page <= 1;
  $('#online-next').disabled = page >= pageCount;
  $('#online-pagination').classList.toggle('hidden', pageCount <= 1);
  hydrateOnlineMetadata(items);
}

function renderOnlineCategorySelection() {
  $$('[data-online-category-browse]').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.onlineCategoryBrowse === state.online.browseCategory);
  });
}

function renderOnlineHome() {
  const container = $('#online-home-sections');
  const sections = state.online.homeSections;
  container.innerHTML = sections.map((section) => `<section class="online-ranking-section">
    <div class="online-ranking-heading"><div><strong>${escapeHtml(section.title)}</strong><small>${escapeHtml(section.subtitle || '')}</small></div></div>
    <div class="online-grid online-ranking-grid">${(section.items || []).map((comic, index) => onlineCardTemplate(comic, index)).join('')}</div>
  </section>`).join('');
  const status = $('#online-home-status');
  status.classList.toggle('hidden', sections.length > 0);
  status.textContent = state.online.homeLoading ? '正在加载榜单…' : '暂无榜单';
  const updated = formatSnapshotTime(state.online.homeUpdatedAt);
  $('#online-home-updated').textContent = updated ? `更新 ${updated}` : '正在加载…';
  bindOnlineImages(container);
  const items = sections.flatMap((section) => section.items || []);
  hydrateOnlineMetadata(items);
}

async function loadOnlineHome({ poll = false } = {}) {
  if (state.online.homeLoaded && !poll) return;
  if (state.online.homeLoading) return;
  state.online.homeLoading = true;
  renderOnlineHome();
  try {
    const response = await api('/api/online/home');
    state.online.homeLoaded = true;
    state.online.homeUpdatedAt = response.updatedAt || '';
    state.online.homeReason = response.reason || '';
    state.online.homeSections = (response.sections || []).map((section) => ({
      ...section,
      items: (section.items || []).map(remoteOnlineComic),
    }));
    clearTimeout(state.online.homePollTimer);
    if (response.refreshing && !state.online.homeSections.length) {
      state.online.homePollTimer = setTimeout(() => loadOnlineHome({ poll: true }).catch(() => {}), 2500);
    }
  } catch (error) {
    state.online.homeReason = `榜单读取失败：${error.message}`;
  } finally {
    state.online.homeLoading = false;
    renderOnlineHome();
  }
}

async function browseOnlineCategory(category, page = 1) {
  const selected = Object.prototype.hasOwnProperty.call(ONLINE_CATEGORY_LABELS, category) ? category : 'all';
  const requestId = (state.online.requestId || 0) + 1;
  state.online.requestId = requestId;
  state.online.browseCategory = selected;
  state.online.loading = true;
  state.online.hasSearched = true;
  state.online.response = { resultKind: 'category', category: selected, page, pageCount: 1, total: 0 };
  state.online.results = [];
  $('#online-category').value = selected;
  setOnlineView('categories');
  renderOnlineCategorySelection();
  renderOnlineResults();
  $('#online-status').classList.remove('hidden');
  $('#online-status').textContent = `正在读取${ONLINE_CATEGORY_LABELS[selected]}分类…`;
  $('#online-grid').classList.add('is-loading');
  const selectedTime = $('#online-category-time').value;
  const params = new URLSearchParams({
    page: String(page),
    category: selected,
    sort: $('#online-category-sort').value,
    timeRange: ['all', 'today', 'week', 'month'].includes(selectedTime) ? selectedTime : 'all',
  });
  try {
    const response = await api(`/api/online/categories?${params}`);
    if (requestId !== state.online.requestId) return;
    state.online.response = response;
    state.online.results = (response.items || []).map(remoteOnlineComic);
    markProviderReachability(true);
    $('#online-status').classList.add('hidden');
    renderOnlineResults();
    $('.online-results-block').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    if (requestId !== state.online.requestId) return;
    state.online.results = [];
    loadProvider().catch(() => {});
    $('#online-status').textContent = `分类读取失败：${error.message}`;
    renderOnlineResults();
  } finally {
    if (requestId === state.online.requestId) {
      state.online.loading = false;
      $('#online-grid').classList.remove('is-loading');
      renderOnlineResults();
    }
  }
}

function openOnlineCategories() {
  setOnlineView('categories');
  if (state.online.browseCategory) {
    renderOnlineCategorySelection();
    renderOnlineResults();
    return;
  }
  browseOnlineCategory('all', 1);
}

function loadOnlinePage(page = 1) {
  if (state.online.browseCategory) return browseOnlineCategory(state.online.browseCategory, page);
  return searchOnline(page);
}

function rerunOnlineResults() {
  if (!state.online.hasSearched) return;
  return loadOnlinePage(1);
}

async function loadOnlineRecommendations({ refresh = false, poll = false } = {}) {
  if (state.online.recommendationLoaded && !refresh && !poll) return;
  if (state.online.recommendationLoading) return;
  if (refresh) state.online.recommendationBatch += 1;
  state.online.recommendationLoading = true;
  const filterRevision = state.online.recommendationFilterRevision;
  $('#online-recommend-status').classList.remove('hidden');
  $('#online-recommend-status').textContent = '正在加载推荐…';
  setBusy($('#online-refresh-recommendations'), true, '加载中…');
  try {
    const params = new URLSearchParams({
      limit: '12',
      batch: String(state.online.recommendationBatch),
      sort: state.online.recommendationSort,
    });
    state.online.excludedRecommendationTags.forEach((tag) => params.append('excludeTag', tag));
    const response = await api(`/api/online/recommendations?${params}`);
    state.online.recommendationLoaded = true;
    state.online.recommendationSeed = response.seed;
    state.online.recommendationProfile = response.profile;
    state.online.recommendationReason = response.reason || '';
    state.online.recommendationUpdatedAt = response.updatedAt || '';
    state.online.recommendationRefreshing = Boolean(response.refreshing);
    state.online.recommendationBatch = Number(response.batch ?? state.online.recommendationBatch);
    state.online.recommendationBatchCount = Math.max(1, Number(response.batchCount || 1));
    state.online.recommendationExcludedCount = Math.max(0, Number(response.excludedCount || 0));
    state.online.recommendations = (response.items || []).map(remoteOnlineComic);
    clearTimeout(state.online.recommendationPollTimer);
    if (response.refreshing) {
      state.online.recommendationPollTimer = setTimeout(() => loadOnlineRecommendations({ poll: true }).catch(() => {}), 2500);
    }
  } catch (error) {
    if (refresh) state.online.recommendationBatch = Math.max(0, state.online.recommendationBatch - 1);
    state.online.recommendationReason = `推荐读取失败：${error.message}`;
    if (state.activeCollection === 'jm-online') toast(`推荐读取失败：${error.message}`, 'error', 5200);
  } finally {
    state.online.recommendationLoading = false;
    setBusy($('#online-refresh-recommendations'), false);
    renderOnlineRecommendations();
    if (filterRevision !== state.online.recommendationFilterRevision) {
      loadOnlineRecommendations({ poll: true }).catch(() => {});
    }
  }
}

async function searchOnline(page = 1) {
  const query = $('#online-query').value.trim();
  if (!query) {
    $('#online-query').focus();
    return toast('请输入在线搜索关键词', 'error');
  }
  const requestId = (state.online.requestId || 0) + 1;
  state.online.browseCategory = null;
  renderOnlineCategorySelection();
  setOnlineView('search');
  state.online.requestId = requestId;
  const alreadyLoading = state.online.loading;
  state.online.loading = true;
  state.online.hasSearched = true;
  $('#online-status').classList.remove('hidden');
  $('#online-status').textContent = '正在连接 JM 并检索…';
  $('#online-grid').classList.add('is-loading');
  if (!alreadyLoading) setBusy($('#online-submit'), true, '搜索中…');
  const params = new URLSearchParams({
    q: query,
    page: String(page),
    mode: $('#online-mode').value,
    sort: $('#online-sort').value,
    timeRange: $('#online-time').value,
    category: $('#online-category').value,
  });
  if ($('#online-time').value === 'custom') {
    if ($('#online-date-from').value) params.set('dateFrom', $('#online-date-from').value);
    if ($('#online-date-to').value) params.set('dateTo', $('#online-date-to').value);
  }
  try {
    const response = await api(`/api/online/search?${params}`);
    if (requestId !== state.online.requestId) return;
    state.online.response = response;
    state.online.results = (response.items || []).map(remoteOnlineComic);
    markProviderReachability(true);
    rememberOnlineSearch(query, $('#online-mode').value);
    $('#online-status').classList.add('hidden');
    renderOnlineResults();
    $('.online-results-block').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    if (requestId !== state.online.requestId) return;
    state.online.results = [];
    loadProvider().catch(() => {});
    $('#online-status').textContent = `搜索失败：${error.message}`;
    renderOnlineResults();
  } finally {
    if (requestId === state.online.requestId) {
      state.online.loading = false;
      $('#online-grid').classList.remove('is-loading');
      setBusy($('#online-submit'), false);
      renderOnlineResults();
    }
  }
}

function onlineComicById(sourceId) {
  return onlineItems()
    .find((comic) => String(comic.sourceId) === String(sourceId));
}

function updateOnlineShelfState(sourceId, patch = {}) {
  for (const comic of onlineItems()) {
    if (String(comic.sourceId) === String(sourceId)) Object.assign(comic, { inShelf: true, ...patch });
  }
  renderOnlineResults();
  renderOnlineRecommendations();
  renderOnlineHome();
}

async function loadCollections() {
  state.collections = await api('/api/collections');
  renderCollections();
}

function seriesStateSignature(seriesItems) {
  return JSON.stringify(seriesItems.map((series) => [
    series.id,
    series.kind,
    series.memberIds,
    series.updateAvailableCount,
    series.lastCheckedAt,
    series.updateError,
  ]));
}

async function loadSeries({ onlyIfChanged = false } = {}) {
  const previousSignature = seriesStateSignature(state.series);
  const nextSeries = await api('/api/library-series');
  state.series = nextSeries;
  if (!onlyIfChanged || seriesStateSignature(nextSeries) !== previousSignature) renderLibrary();
}

function renderProviderStatus() {
  if (!state.provider) return;
  const connected = state.provider.available && state.provider.reachable === true;
  const failed = !state.provider.available || state.provider.reachable === false;
  $('#provider-dot').classList.toggle('online', connected);
  $('#provider-label').textContent = failed
    ? 'JM 连接失败'
    : (state.provider.authenticated ? `JM · ${state.provider.username}` : (connected ? '已连接 JM' : 'JM 已就绪'));
  $('#settings-status').textContent = !state.provider.available
    ? state.provider.reason
    : (state.provider.reachable === false
      ? `无法连接 JM${state.provider.connectionError ? ` · ${state.provider.connectionError}` : ''}`
      : `${connected ? '已连接 JM' : 'JM 组件已就绪'} · jmcomic ${state.provider.version}`);
  $('#settings-status').classList.toggle('error', failed);
  $('#account-status').textContent = state.provider.authenticated ? `已登录 ${state.provider.username}` : '未登录';
  $('#account-status').classList.toggle('is-online', state.provider.authenticated);
  $('#account-username').value = state.provider.username || $('#account-username').value;
  $('#account-logout').classList.toggle('hidden', !state.provider.authenticated);
}

function markProviderReachability(reachable, error = '') {
  if (!state.provider) return;
  state.provider.reachable = reachable;
  state.provider.connectionError = reachable ? '' : String(error || '连接失败');
  renderProviderStatus();
}

async function loadProvider({ probe = false } = {}) {
  state.provider = await api(`/api/provider/status${probe ? '?probe=true' : ''}`);
  renderProviderStatus();
}

async function loginProviderAccount() {
  const button = $('#account-login');
  const username = $('#account-username').value.trim();
  const password = $('#account-password').value;
  if (!username || !password) return toast('请输入 JM 账号和密码', 'error');
  setBusy(button, true, '登录中…');
  try {
    await api('/api/provider/login', { method: 'POST', body: { username, password } });
    $('#account-password').value = '';
    await loadProvider();
    await loadJmFavorites({ refresh: true, render: state.activeCollection === 'jm-favorites' });
    toast(`已登录 JM 账号 ${username}`);
  } catch (error) { toast(`登录失败：${error.message}`, 'error', 6500); }
  finally { setBusy(button, false); }
}

async function logoutProviderAccount() {
  try {
    await api('/api/provider/login', { method: 'DELETE' });
    $('#account-username').value = '';
    $('#account-password').value = '';
    await loadProvider();
    state.jmFavorites = null;
    $('#jm-favorites-count').textContent = '—';
    if (state.activeCollection === 'jm-favorites') await loadLibrary();
    toast('已退出 JM 账号');
  } catch (error) { toast(error.message, 'error'); }
}

function renderDownloadQueue() {
  const anchor = $('#download-queue-anchor');
  const active = state.downloads.filter((task) => ['queued', 'running'].includes(task.status));
  const items = [...active, ...state.downloads.filter((task) => !['queued', 'running'].includes(task.status))].slice(0, 7);
  anchor.classList.toggle('has-downloads', state.downloads.length > 0);
  anchor.classList.toggle('is-active', active.length > 0);
  $('#download-queue-summary').textContent = active.length ? `${active.length} 项进行中` : (items.length ? '最近任务' : '暂无任务');
  const badge = $('#download-queue-badge');
  badge.textContent = String(active.length);
  badge.classList.toggle('hidden', active.length === 0);
  const list = $('#download-queue-list');
  const existing = new Map($$('[data-download-task]', list).map((item) => [item.dataset.downloadTask, item]));
  const visibleIds = new Set(items.map((task) => task.id));
  existing.forEach((item, taskId) => { if (!visibleIds.has(taskId)) item.remove(); });
  $('.download-queue-empty', list)?.remove();
  items.forEach((task, index) => {
    const statusIcon = task.status === 'completed' ? 'tick' : task.status === 'failed' ? 'close' : task.status === 'queued' ? 'list' : 'download';
    const statusLabel = task.status === 'queued' && task.queuePosition ? `队列第 ${task.queuePosition} 项` : task.phase;
    let item = existing.get(task.id);
    if (!item) {
      item = document.createElement('article');
      item.dataset.downloadTask = task.id;
      item.className = 'download-queue-item';
      item.innerHTML = `<span class="download-task-icon"></span>
        <span class="download-task-body"><strong></strong><small></small><span class="download-progress"><i></i></span></span>
        <span class="download-task-tail"><b></b><button type="button" class="download-retry hidden" data-action="retry-download" aria-label="重试下载" title="重试">${icon('refresh')}</button></span>`;
    }
    item.style.setProperty('--reveal-index', index);
    item.className = `download-queue-item is-${task.status}`;
    const taskIcon = $('.download-task-icon', item);
    if (taskIcon.dataset.icon !== statusIcon) {
      taskIcon.dataset.icon = statusIcon;
      taskIcon.innerHTML = icon(statusIcon);
    }
    const title = $('strong', item);
    title.textContent = task.title;
    title.title = task.title;
    const detail = $('small', item);
    detail.textContent = task.error || statusLabel;
    detail.title = task.error || statusLabel;
    $('.download-progress i', item).style.width = `${Number(task.progress || 0)}%`;
    $('.download-task-tail b', item).textContent = `${Number(task.progress || 0)}%`;
    $('.download-retry', item).classList.toggle('hidden', task.status !== 'failed');
    if (list.children[index] !== item) list.insertBefore(item, list.children[index] || null);
  });
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'download-queue-empty';
    empty.textContent = '选择下载后，任务进度会显示在这里';
    list.append(empty);
  }
}

async function retryDownload(taskId) {
  const response = await api(`/api/downloads/${taskId}/retry`, { method: 'POST' });
  await loadDownloads({ notify: false });
  toast(response.created ? '已重新加入下载队列' : '该任务已经在队列中');
}

async function loadDownloads({ notify = true } = {}) {
  if (state.downloadPollBusy) return;
  state.downloadPollBusy = true;
  try {
    const previous = new Map(state.downloads.map((task) => [task.id, task.status]));
    const previousImported = new Map(state.downloads.map((task) => [task.id, Number(task.importedItems || 0)]));
    const snapshot = await api('/api/downloads');
    state.downloads = snapshot.items || [];
    renderDownloadQueue();
    if (notify && state.downloadStateReady) {
      const completed = state.downloads.filter((task) => task.status === 'completed' && previous.get(task.id) !== 'completed');
      const failed = state.downloads.filter((task) => task.status === 'failed' && previous.get(task.id) !== 'failed');
      const newlyImported = state.downloads.some((task) => Number(task.importedItems || 0) > (previousImported.get(task.id) || 0));
      if (completed.length || newlyImported) {
        await Promise.all([loadLibrary(), loadCollections(), loadSeries()]);
      }
      if (completed.length) {
        completed.forEach((task) => toast(`「${task.title}」下载完成并已加入书架`));
      }
      failed.forEach((task) => toast(`「${task.title}」下载失败：${task.error || '未知错误'}`, 'error', 6500));
    }
    state.downloadStateReady = true;
  } catch (error) {
    if (notify) toast(`下载队列更新失败：${error.message}`, 'error');
  } finally {
    state.downloadPollBusy = false;
  }
}

function activeCacheTaskForComic(comic) {
  if (!comic?.sourceId) return null;
  return state.caches.find((task) => {
    if (!['queued', 'running'].includes(task.status)) return false;
    const selected = task.context?.selectedSourceIds || [];
    return String(task.sourceId) === String(comic.sourceId)
      || selected.some((sourceId) => String(sourceId) === String(comic.sourceId));
  }) || null;
}

async function loadCaches({ notify = true } = {}) {
  if (state.cachePollBusy) return;
  state.cachePollBusy = true;
  try {
    const previous = new Map(state.caches.map((task) => [task.id, task.status]));
    const snapshot = await api('/api/caches');
    state.caches = snapshot.items || [];
    if (notify && state.cacheStateReady) {
      const completed = state.caches.filter((task) => task.status === 'completed' && previous.get(task.id) !== 'completed');
      const failed = state.caches.filter((task) => task.status === 'failed' && previous.get(task.id) !== 'failed');
      if (completed.length) {
        await Promise.all([loadLibrary(), loadCollections(), loadSeries()]);
        completed.forEach((task) => toast(`「${task.title}」已缓存，可以直接阅读`));
      }
      failed.forEach((task) => toast(`「${task.title}」缓存失败：${task.error || '未知错误'}`, 'error', 6500));
    }
    state.cacheStateReady = true;
  } catch (error) {
    if (notify) toast(`缓存状态更新失败：${error.message}`, 'error');
  } finally {
    state.cachePollBusy = false;
  }
}

async function cacheComics(comics, { notify = true, priority = false, progressive = false } = {}) {
  const comicIds = [...new Set((comics || []).filter((comic) => comic?.sourceId).map((comic) => comic.id))];
  if (!comicIds.length) return { tasks: [], createdCount: 0, skippedCount: 0 };
  const result = await api('/api/caches', {
    method: 'POST',
    body: {
      comicIds,
      progressive: progressive || priority,
      priorityComicId: priority ? comicIds[0] : null,
    }
  });
  await loadCaches({ notify: false });
  if (notify) toast(result.createdCount ? '已加入阅读缓存队列' : '这些条目已缓存或正在缓存');
  return result;
}

async function clearComicCaches(comics) {
  const cached = (comics || []).filter((comic) => comic?.storageKind === 'cache');
  if (!cached.length) return;
  const result = await api('/api/caches/clear', {
    method: 'POST',
    body: { comicIds: cached.map((comic) => comic.id) }
  });
  await Promise.all([loadLibrary(), loadSeries()]);
  toast(result.clearedCount > 1 ? `已清理 ${result.clearedCount} 话阅读缓存` : '阅读缓存已清理');
}

function renderCollections() {
  const byParent = new Map();
  for (const item of state.collections) {
    const key = item.parentId || 'root';
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(item);
  }
  const visited = new Set();
  let revealIndex = 0;
  const branch = (parentId = 'root', depth = 0) => (byParent.get(parentId) || []).map((item) => {
    if (visited.has(item.id)) return '';
    visited.add(item.id);
    const itemRevealIndex = revealIndex++;
    return `<div class="collection-row" data-collection-row="${item.id}" style="--reveal-index:${itemRevealIndex}">
      <button class="collection-item ${state.activeCollection === item.id ? 'is-active' : ''}" data-collection="${item.id}" style="--depth:${depth}">${icon('folder', 'collection-folder')}<span>${escapeHtml(item.name)}</span><small>${item.comicCount}</small></button>
      <button class="collection-manage" data-action="manage-collection" data-collection-id="${item.id}" aria-label="管理目录 ${escapeHtml(item.name)}" title="重命名、移动或删除">${icon('settings')}</button>
    </div>${branch(item.id, depth + 1)}`;
  }).join('');
  $('#collection-tree').innerHTML = branch() || '<p style="padding:6px 11px;color:#50535a;font-size:10px">还没有目录</p>';
  $$('.nav-item').forEach((item) => item.classList.toggle('is-active', item.dataset.collection === state.activeCollection));
}

function libraryStorageBadge(items) {
  const comics = Array.isArray(items) ? items : [items];
  const saved = comics.some((comic) => comic?.rootPath && ['local', 'download'].includes(comic.storageKind));
  const cached = comics.some((comic) => comic?.rootPath && comic.storageKind === 'cache');
  if (saved) return `<span class="library-storage-mark" title="已保存到本地">${icon('save')}</span>`;
  if (cached) return `<span class="library-storage-mark is-cache" title="应用阅读缓存">${icon('file')}</span>`;
  return '';
}

function cardTemplate(comic, revealIndex = 0) {
  const remoteFavorite = Boolean(comic.remoteFavorite);
  const author = comic.authors.join(' · ') || (comic.sourceStatus === 'pending' ? '等待匹配信息' : '未知作者');
  const progress = comic.pageCount ? Math.round(((comic.progressPage + 1) / comic.pageCount) * 100) : 0;
  const readPosition = comic.lastReadAt && comic.pageCount ? ` · 已看到 ${Math.min(comic.progressPage + 1, comic.pageCount)}P` : '';
  const coverUrl = remoteFavorite ? comic.coverUrl : (comic.coverPath ? comicCoverUrl(comic) : '');
  return `<article class="library-card comic-card ${remoteFavorite ? 'remote-favorite-card' : ''}" data-comic-id="${comic.id}" draggable="${remoteFavorite ? 'false' : 'true'}" tabindex="0" title="${remoteFavorite ? 'JM 收藏 · 尚未下载，点击查看详情' : '拖到左侧收藏目录，或点击查看详情'}" style="--reveal-index:${revealIndex}">
    <div class="card-cover">
      <div class="card-fallback"></div>
      ${coverUrl ? `<img src="${coverUrl}" alt="${escapeHtml(comic.displayName)} 的封面" loading="lazy">` : ''}
      ${coverPrivacyMask(comic)}
      ${remoteFavorite ? '' : libraryStorageBadge(comic)}
      ${remoteFavorite ? `<span class="remote-favorite-mark">${icon('download')}尚未下载</span>` : ''}
      ${progress ? `<span class="progress-track"><i style="width:${progress}%"></i></span>` : ''}
    </div>
    <h3 class="card-title" title="${escapeHtml(comic.displayName)}">${escapeHtml(comic.displayName)}</h3>
    <div class="card-meta">${escapeHtml(author)}${comic.pageCount ? ` · ${comic.pageCount}P` : ''}${readPosition}</div>
  </article>`;
}

function seriesResumeComic(series) {
  if (!series?.members?.length) return null;
  return series.members.find((comic) => comic.id === series.resumeComicId)
    || series.members.filter((comic) => comic.lastReadAt).sort((a, b) => String(b.lastReadAt).localeCompare(String(a.lastReadAt)))[0]
    || series.members[0];
}

function seriesResumeLabel(series, comic = seriesResumeComic(series)) {
  if (!comic) return '暂无可阅读章节';
  const chapter = Math.max(1, series.memberIds.indexOf(comic.id) + 1);
  return comic.lastReadAt ? `上次看到 第 ${chapter} 话 · 第 ${Number(comic.progressPage || 0) + 1} 页` : '尚未开始阅读';
}

function seriesCheckLabel(value) {
  if (!value) return '等待首次自动检查';
  const checked = new Date(value);
  if (Number.isNaN(checked.getTime())) return '已检查';
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - checked.getTime()) / 60000));
  if (elapsedMinutes < 1) return '刚刚检查';
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前检查`;
  if (elapsedMinutes < 24 * 60) return `${Math.floor(elapsedMinutes / 60)} 小时前检查`;
  return `${checked.toLocaleDateString('zh-CN')} 检查`;
}

function seriesCardTemplate(series, revealIndex = 0) {
  const resume = seriesResumeComic(series);
  const updateCount = Number(series.updateAvailableCount || 0);
  const isSourceSeries = series.kind === 'source' || series.isSourceSeries;
  if (isSourceSeries) {
    const cover = series.members[0];
    return `<article class="library-card series-card source-series-card ${updateCount ? 'has-series-update' : ''}" data-series-id="${series.id}" draggable="true" tabindex="0" title="JM 原生多 P 作品 · 可拖到左侧收藏目录" style="--reveal-index:${revealIndex}">
      <div class="source-series-card-cover card-cover">
        <div class="card-fallback"></div>
        ${cover?.coverPath ? `<img src="${comicCoverUrl(cover)}" alt="${escapeHtml(series.displayName)} 的默认封面" loading="lazy">` : ''}
        ${coverPrivacyMask(cover)}
        <span class="source-series-mark">禁漫书库</span>
        ${libraryStorageBadge(series.members)}
        ${updateCount ? `<span class="series-update-badge">${icon('refresh')}${updateCount} P 更新</span>` : ''}
        <span class="series-count">${series.members.length} P</span>
      </div>
      <h3 class="card-title" title="${escapeHtml(series.displayName)}">${escapeHtml(series.displayName)}</h3>
      <div class="series-progress-note source-series-progress">${icon('book')}<span>${escapeHtml(seriesResumeLabel(series, resume))}</span></div>
    </article>`;
  }
  const covers = series.members.slice(0, 3).map((comic, index) => `<div class="series-cover-layer" style="--stack:${index}">
    ${comic.coverPath ? `<img src="${comicCoverUrl(comic)}" alt="${escapeHtml(comic.displayName)} 的封面" loading="lazy">` : '<div class="series-cover-empty"></div>'}${coverPrivacyMask(comic)}
  </div>`).join('');
  return `<article class="library-card series-card ${updateCount ? 'has-series-update' : ''}" data-series-id="${series.id}" draggable="true" tabindex="0" title="拖到左侧收藏目录，或点击打开系列" style="--reveal-index:${revealIndex}">
    <div class="series-cover-stack">${covers}${libraryStorageBadge(series.members)}${updateCount ? `<span class="series-update-badge">${icon('refresh')}${updateCount} 话更新</span>` : ''}<span class="series-count">${series.members.length} 话</span></div>
    <h3 class="card-title" title="${escapeHtml(series.displayName)}">${escapeHtml(series.displayName)}</h3>
    ${resume ? `<div class="series-progress-note">${icon(resume.lastReadAt ? 'bookmark' : 'book')}<span>${escapeHtml(seriesResumeLabel(series, resume))}</span></div>` : ''}
  </article>`;
}

function librarySortMetrics(item) {
  const comics = item.kind === 'series' ? (item.value.members || []) : [item.value];
  const dates = (key) => comics.map((comic) => Date.parse(comic[key] || '') || 0);
  return {
    title: String(item.value.displayName || item.value.title || '').toLocaleLowerCase('zh-CN'),
    updated: Math.max(0, ...dates('updatedAtSource'), ...dates('updatedAt')),
    added: Math.max(0, ...dates('createdAt')),
    lastRead: Math.max(0, ...dates('lastReadAt')),
    readCount: comics.reduce((sum, comic) => sum + Number(comic.readCount || 0), 0),
    unreadCount: comics.filter((comic) => !comic.lastReadAt).length,
    pages: comics.reduce((sum, comic) => sum + Number(comic.pageCount || 0), 0),
  };
}

function sortLibraryItems(items) {
  if (state.librarySort === 'default') return items;
  return items.map((item, index) => ({ ...item, index, metrics: librarySortMetrics(item) })).sort((a, b) => {
    if (state.librarySort === 'title') return a.metrics.title.localeCompare(b.metrics.title, 'zh-CN') || a.index - b.index;
    if (state.librarySort === 'unread') {
      return (Number(b.metrics.unreadCount > 0) - Number(a.metrics.unreadCount > 0))
        || (b.metrics.unreadCount - a.metrics.unreadCount)
        || (b.metrics.updated - a.metrics.updated)
        || a.index - b.index;
    }
    const key = ({
      updated: 'updated',
      added: 'added',
      'recent-read': 'lastRead',
      'most-read': 'readCount',
      pages: 'pages',
    })[state.librarySort] || 'updated';
    return (b.metrics[key] - a.metrics[key]) || (b.metrics.updated - a.metrics.updated) || a.index - b.index;
  });
}

function renderLibrary() {
  const visibleIds = new Set(state.comics.map((comic) => comic.id));
  const displaySeries = state.series.filter((series) => series.displayAsSeries !== false);
  const groupedIds = new Set(displaySeries.flatMap((series) => series.memberIds));
  let visibleSeries = displaySeries.filter((series) => series.memberIds.some((comicId) => visibleIds.has(comicId)));
  let standalone = state.comics.filter((comic) => !groupedIds.has(comic.id));
  const allItemCount = visibleSeries.length + standalone.length;
  if (state.libraryFilter === 'custom') {
    visibleSeries = visibleSeries.filter((series) => series.kind !== 'source' && !series.isSourceSeries);
    standalone = [];
  } else if (state.libraryFilter === 'source') {
    visibleSeries = visibleSeries.filter((series) => series.kind === 'source' || series.isSourceSeries);
    standalone = [];
  } else if (state.libraryFilter === 'standalone') {
    visibleSeries = [];
  }
  const sortedItems = sortLibraryItems([
    ...visibleSeries.map((value) => ({ kind: 'series', value })),
    ...standalone.map((value) => ({ kind: 'comic', value })),
  ]);
  $('#comic-grid').innerHTML = sortedItems.map((item, index) => item.kind === 'series'
    ? seriesCardTemplate(item.value, index)
    : cardTemplate(item.value, index)).join('');
  $('#comic-grid').classList.toggle('compact', $('#density-select').value === 'compact');
  const itemCount = visibleSeries.length + standalone.length;
  $('#empty-state').classList.toggle('hidden', itemCount > 0);
  const favoritesSignedOut = state.activeCollection === 'jm-favorites' && !state.provider?.authenticated;
  $('#empty-state h2').textContent = favoritesSignedOut ? '登录后查看 JM 收藏' : (allItemCount ? '这个分类还没有本子' : '书架还是空的');
  $('#empty-state p').textContent = favoritesSignedOut ? '请从左下角登录 JM 账号，登录成功后会读取账号收藏。' : (allItemCount ? '选择其他类型，或清除搜索条件后再看看。' : '导入一个本子目录，或输入车牌从信息源建立条目。');
  $('#result-count').textContent = `${itemCount} 本`;
  $('#all-count').textContent = state.activeCollection ? '—' : allItemCount;
  $('#view-title').textContent = currentCollectionName();
  $$('.library-filter-button').forEach((button) => {
    const active = button.dataset.libraryFilter === state.libraryFilter;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $$('#comic-grid img').forEach((image) => {
    if (image.complete && image.naturalWidth) image.classList.add('loaded');
    image.addEventListener('load', () => image.classList.add('loaded'), { once: true });
    image.addEventListener('error', () => image.remove(), { once: true });
  });
}

function setLibraryFilter(filter) {
  const allowed = new Set(['all', 'custom', 'source', 'standalone']);
  state.libraryFilter = allowed.has(filter) ? filter : 'all';
  localStorage.setItem('jmshelf-library-filter', state.libraryFilter);
  renderLibrary();
}

function findComic(comicId) {
  return state.comics.find((item) => item.id === comicId)
    || state.series.flatMap((series) => series.members).find((item) => item.id === comicId)
    || onlineItems().find((item) => item.id === comicId);
}

function detailSearchMarkup(values, mode, emptyLabel) {
  return (values || []).length
    ? values.map((item, index) => `<button type="button" class="chip chip-button" data-action="online-search-metadata" data-online-mode="${mode}" data-online-query="${escapeHtml(item)}" style="--reveal-index:${index}" title="搜索 ${escapeHtml(item)}">${escapeHtml(item)}</button>`).join('')
    : `<span class="chip">${emptyLabel}</span>`;
}

function detailTagMarkup(tags) {
  return detailSearchMarkup(tags, 'tag', '暂无标签');
}

function renderDetailMetadata(comic) {
  $('#detail-authors').innerHTML = detailSearchMarkup(comic.authors, 'author', '未知作者');
  $('#detail-tags').innerHTML = detailTagMarkup(comic.tags);
  $('#detail-works').innerHTML = detailSearchMarkup(comic.works, 'work', '暂无作品标签');
  $('#detail-actors').innerHTML = detailSearchMarkup(comic.actors, 'actor', '暂无登场人物');
}

function configureOnlineDetailActions(comic) {
  const sourceSeries = Boolean(comic?.remoteOnline && comic.isSourceSeries && comic.episodeManifest?.length > 1);
  $('#detail-preview').classList.toggle('hidden', !comic?.remoteOnline || sourceSeries);
  $('#detail-online-chapters').classList.toggle('hidden', !sourceSeries);
  $('#detail-online-latest').classList.toggle('hidden', !sourceSeries);
  if (sourceSeries) {
    const latest = comic.episodeManifest.at(-1);
    $('#detail-online-latest').title = latest?.title ? `最新一话：${latest.title}` : '查看最新一话';
  }
}

async function searchOnlineMetadata(value, mode = 'tag') {
  const query = String(value || '').trim();
  if (!query) return;
  if ($('#detail-drawer').classList.contains('open')) closeDetail();
  if (state.activeCollection !== 'jm-online') await selectCollection('jm-online');
  startOnlineSearch(query, mode);
}

function showDetail(comicId) {
  const comic = findComic(comicId);
  if (!comic) return;
  state.activeComic = comic;
  const remoteFavorite = isRemoteComic(comic);
  const isSourceSeriesDetail = state.series.some((series) =>
    (series.kind === 'source' || series.isSourceSeries) && series.members.some((member) => member.id === comic.id)
  );
  $('#detail-drawer').classList.toggle('is-source-series-detail', isSourceSeriesDetail);
  $('#detail-drawer').classList.toggle('is-remote-favorite', remoteFavorite);
  $('#detail-display-name').textContent = comic.displayName;
  $('#detail-original-title').textContent = comic.nickname && comic.title ? comic.title : '';
  const series = comic.seriesId && comic.seriesId !== comic.sourceId ? ` · 系列 JM${comic.seriesId} 第${comic.chapterIndex}话` : '';
  $('#detail-source').textContent = remoteFavorite
    ? `JM${comic.favoriteAlbumId || comic.sourceId} · ${comic.remoteOnline ? 'JMonline' : 'JM 收藏'} · ${comic.inShelf ? '已在书架' : '尚未加入书架'}`
    : (comic.sourceId ? `JM${comic.sourceId}${series} · ${comic.pageCount || 0} PAGES` : `LOCAL ITEM · ${comic.pageCount || 0} PAGES`);
  $('#detail-read').classList.toggle('hidden', remoteFavorite);
  configureOnlineDetailActions(comic);
  $('#detail-rematch').classList.toggle('hidden', remoteFavorite);
  $('#detail-favorite-add').classList.toggle('hidden', !remoteFavorite);
  $('#detail-favorite-download').classList.toggle('hidden', !remoteFavorite);
  $('#detail-favorite-add').disabled = Boolean(comic.inShelf);
  $('#detail-favorite-add').innerHTML = comic.inShelf
    ? `${icon('tick')}已在书架`
    : `${icon('plus')}加入书架`;
  $$('.detail-local-only', $('#detail-drawer')).forEach((item) => item.classList.toggle('hidden', remoteFavorite));
  $('#detail-cover-privacy-settings').classList.toggle('hidden', remoteFavorite || !state.settings.sanityMode);
  $('#detail-nickname').value = comic.nickname || '';
  $('#detail-note').value = comic.note || '';
  $('#detail-cover-privacy-enabled').checked = comic.coverPrivacyEnabled !== false;
  $('#detail-cover-privacy-direction').value = comic.coverPrivacyDirection === 'above' ? 'above' : 'below';
  $('#detail-cover-privacy-start').value = Math.max(15, Math.min(85, Number(comic.coverPrivacyStart || 58)));
  $('#detail-cover-privacy-value').textContent = `${$('#detail-cover-privacy-start').value}%`;
  $('#detail-path').textContent = comic.rootPath || '尚未关联本地文件';
  renderDetailMetadata(comic);
  $('#detail-collections').innerHTML = state.collections.length ? state.collections.map((item, index) => `<label style="--reveal-index:${index}"><input type="checkbox" value="${item.id}" ${comic.collections.includes(item.id) ? 'checked' : ''}>${escapeHtml(item.name)}</label>`).join('') : '<span class="muted">还没有收藏夹</span>';
  renderDetailCover(comic);
  $('#detail-scrim').classList.remove('hidden');
  $('#detail-drawer').classList.add('open');
  $('#detail-drawer').setAttribute('aria-hidden', 'false');
  if (remoteFavorite && !comic.detailLoaded) enrichRemoteFavorite(comic);
}

async function enrichRemoteFavorite(comic) {
  try {
    const metadata = comic.remoteOnline
      ? await api('/api/online/metadata', {
        method: 'POST',
        body: { albumIds: [comic.favoriteAlbumId || comic.sourceId], priority: true }
      })
      : await api('/api/source/lookup', { method: 'POST', body: { plate: comic.favoriteAlbumId || comic.sourceId } });
    if (state.activeComic?.id !== comic.id) return;
    const first = metadata.items?.[0] || metadata;
    if (!first?.albumId && comic.remoteOnline) throw new Error('在线详情暂时不可用');
    const episodeCount = comic.remoteOnline
      ? Number(comic.favoriteEpisodeCount || 0)
      : Number(metadata.itemCount || metadata.items?.length || 1);
    Object.assign(comic, {
      title: metadata.seriesTitle || first.title || comic.title,
      displayName: metadata.seriesTitle || first.title || comic.displayName,
      authors: first.authors || comic.authors,
      tags: first.tags || comic.tags,
      works: first.works || comic.works || [],
      actors: first.actors || comic.actors || [],
      description: first.description || comic.description,
      episodeManifest: first.episodeManifest || metadata.episodeManifest || comic.episodeManifest || [],
      favoriteEpisodeCount: episodeCount,
      metadataLoaded: true,
      detailLoaded: true,
    });
    comic.episodeCount = comic.episodeManifest.length || episodeCount;
    comic.firstPhotoId = String(first.firstPhotoId || comic.episodeManifest[0]?.sourceId || comic.firstPhotoId || '');
    comic.latestSourceId = String(comic.episodeManifest.at(-1)?.sourceId || comic.latestSourceId || '');
    comic.isSourceSeries = comic.episodeManifest.length > 1;
    $('#detail-display-name').textContent = comic.displayName;
    const episodeLabel = comic.favoriteEpisodeCount ? ` · ${comic.favoriteEpisodeCount} 话` : '';
    $('#detail-source').textContent = `JM${comic.favoriteAlbumId || comic.sourceId} · ${comic.remoteOnline ? 'JMonline' : 'JM 收藏'}${episodeLabel} · ${comic.inShelf ? '已在书架' : '尚未加入书架'}`;
    renderDetailMetadata(comic);
    configureOnlineDetailActions(comic);
  } catch (error) {
    if (state.activeComic?.id === comic.id) toast(`详情补全失败：${error.message}`, 'error', 5200);
  }
}

function closeDetail() {
  $('#detail-drawer').classList.remove('open');
  $('#detail-drawer').setAttribute('aria-hidden', 'true');
  $('#detail-scrim').classList.add('hidden');
  state.activeComic = null;
}

async function importFolder() {
  const button = $('#import-folder');
  setBusy(button, true, '选择中…');
  try {
    const selected = await api('/api/dialog/folder', { method: 'POST' });
    if (!selected.path) return;
    setBusy(button, true, '扫描中…');
    const imported = await api('/api/import/folder', { method: 'POST', body: { path: selected.path } });
    toast(`已导入 ${imported.length} 个本子目录`);
    await loadLibrary();
    const pending = imported.filter((item) => item.sourceId && item.sourceStatus === 'pending');
    if (pending.length) autoMatch(pending);
  } catch (error) {
    toast(error.message, 'error', 5000);
  } finally {
    setBusy(button, false);
  }
}

async function autoMatch(comics) {
  if (!state.provider?.available) {
    toast(`发现 ${comics.length} 个纯数字目录；配置 JM 信息源后可自动补全`, 'error', 5200);
    return;
  }
  toast(`正在后台匹配 ${comics.length} 个纯数字目录…`, 'info', 4500);
  let matched = 0;
  for (const comic of comics) {
    try {
      await api('/api/source/lookup', { method: 'POST', body: { plate: comic.sourceId, comicId: comic.id } });
      matched += 1;
    } catch (error) {
      await api(`/api/comics/${comic.id}`, { method: 'PATCH', body: { sourceStatus: 'pending', sourceError: error.message } }).catch(() => {});
    }
  }
  await Promise.all([loadLibrary(), loadSeries()]);
  toast(`自动匹配完成：${matched}/${comics.length}`);
}

function openQuery() {
  state.queryMetadata = null;
  $('#query-result').classList.add('hidden');
  $('#query-status').classList.add('hidden');
  openModal('query-dialog');
  setTimeout(() => $('#plate-input').focus(), 50);
}

function renderQueryResult(metadata) {
  const items = metadata.items?.length ? metadata.items : [metadata];
  const downloadLabel = state.settings.downloadPath ? '加入下载队列' : '选择目录并加入队列';
  const existingCount = items.filter((item) => item.inShelf).length;
  const allExisting = existingCount === items.length;
  const shelfNotice = existingCount
    ? `<p class="series-notice">${allExisting ? '这部作品已在书架中' : `已有 ${existingCount}/${items.length} 话在书架中，加入时只补齐缺少章节`}</p>`
    : '';
  if (items.length > 1) {
    const cover = items[0];
    const preview = items.length <= 5
      ? items.map((item, index) => ({ item, index }))
      : [
          ...items.slice(0, 4).map((item, index) => ({ item, index })),
          { gap: true },
          { item: items.at(-1), index: items.length - 1 },
        ];
    const chapterNames = preview.map((entry) => entry.gap
      ? `<li class="query-series-gap">${icon('list')}<span>还有 ${items.length - 5} P</span></li>`
      : `<li><b>P${escapeHtml(entry.item.chapterIndex || entry.index + 1)}</b><span title="${escapeHtml(entry.item.title)}">${escapeHtml(entry.item.title)}</span>${entry.index === items.length - 1 ? '<em>最新</em>' : ''}</li>`
    ).join('');
    $('#query-result').innerHTML = `${shelfNotice}<div class="query-source-series">
      <div class="query-series-cover"><img class="query-cover-fallback" src="/ukp.png" alt="">${cover.coverUrl ? `<img src="${escapeHtml(cover.coverUrl)}" alt="${escapeHtml(metadata.seriesTitle || cover.title)} 的默认封面" loading="eager" decoding="async">` : ''}<span>${items.length} P</span></div>
      <div class="query-series-copy"><p class="eyebrow">JM${escapeHtml(metadata.seriesId || metadata.queryId || cover.sourceId)} · 原生多 P</p><h3>${escapeHtml(metadata.seriesTitle || cover.title)}</h3><ol>${chapterNames}</ol></div>
    </div><div class="query-actions">
      <button type="button" class="button primary" data-action="add-query-result"${allExisting ? ' disabled' : ''}>${icon(allExisting ? 'tick' : 'plus')}${allExisting ? '已在书架' : (existingCount ? '补齐到书架' : '全部加入书架')}</button>
      <button type="button" class="button ghost" data-action="download-query-result">${icon('download')}${downloadLabel}</button>
    </div>`;
    $('#query-result').classList.remove('hidden');
    $$('.query-series-cover img:not(.query-cover-fallback)').forEach(image => image.addEventListener('error', () => image.remove(), { once: true }));
    return;
  }
  const cards = items.map((item, index) => {
    const tags = (item.tags || []).slice(0, 6).map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join('');
    const authors = (item.authors || []).join(' · ') || '未知作者';
    const pages = Number(item.pageCount || 0) > 0 ? `${Number(item.pageCount)} 页` : '页数未知';
    const warning = item.sourceWarning ? `<p class="query-warning">${escapeHtml(item.sourceWarning)}</p>` : '';
    return `<article class="query-item" style="--reveal-index:${index}">
      <div class="query-cover"><img class="query-cover-fallback" src="/ukp.png" alt="">${item.coverUrl ? `<img src="${escapeHtml(item.coverUrl)}" alt="JM${escapeHtml(item.sourceId)} 默认封面" loading="lazy" decoding="async">` : ''}</div>
      <div class="query-item-body">
        <p class="eyebrow">JM${escapeHtml(item.sourceId)}${items.length > 1 ? ` · 第 ${Number(item.chapterIndex || index + 1)} 话` : ''}${item.inShelf ? ' · 已在书架' : ''}</p>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(authors)} · ${pages}</p>
        <div class="chip-list">${tags}</div>${warning}
      </div>
      ${items.length > 1 ? `<button type="button" class="button ghost query-add-one" data-action="add-query-item" data-query-index="${index}">${icon('plus')}只加入这话</button>` : ''}
    </article>`;
  }).join('');
  const countText = '';
  $('#query-result').innerHTML = `${shelfNotice}<div class="query-summary">
    ${countText ? `<p class="series-notice">${countText}</p>` : ''}
    <div class="query-item-list">${cards}</div>
  </div><div class="query-actions">
    <button type="button" class="button primary" data-action="add-query-result"${allExisting ? ' disabled' : ''}>${icon(allExisting ? 'tick' : 'plus')}${allExisting ? '已在书架' : (items.length > 1 ? '全部加入书架' : '加入书架')}</button>
    <button type="button" class="button ghost" data-action="download-query-result">${icon('download')}${downloadLabel}</button>
  </div>`;
  $('#query-result').classList.remove('hidden');
  $$('.query-cover img:not(.query-cover-fallback)').forEach(image => image.addEventListener('error', () => image.remove(), { once: true }));
}

async function queryPlate(event) {
  event.preventDefault();
  const button = $('#query-form button[type="submit"]');
  setBusy(button, true, '查询中…');
  $('#query-result').classList.add('hidden');
  $('#query-status').classList.remove('hidden', 'error');
  $('#query-status').textContent = '正在获取标题与章节信息…';
  try {
    state.queryMetadata = await api('/api/source/lookup', { method: 'POST', body: { plate: $('#plate-input').value } });
    markProviderReachability(true);
    $('#query-status').classList.add('hidden');
    renderQueryResult(state.queryMetadata);
  } catch (error) {
    loadProvider().catch(() => {});
    $('#query-status').textContent = error.message;
    $('#query-status').classList.add('error');
  } finally {
    setBusy(button, false);
  }
}

async function addQueryResult(index = null) {
  if (!state.queryMetadata) return;
  try {
    const items = state.queryMetadata.items?.length ? state.queryMetadata.items : [state.queryMetadata];
    const requested = index === null ? items : [items[index]];
    const selected = requested.filter((item) => !item.inShelf);
    if (!selected.length) return toast('这部作品已经在书架中');
    const comics = [];
    for (const item of selected) {
      comics.push(await api('/api/comics', { method: 'POST', body: { ...item, sourceStatus: 'matched' } }));
    }
    let cacheError = null;
    try { await cacheComics(comics, { notify: false }); }
    catch (error) { cacheError = error; }
    closeModal('query-dialog');
    await Promise.all([loadLibrary(), loadSeries()]);
    toast(selected.length > 1 ? `已将 ${selected.length} 话分别加入书架` : `已将「${comics[0].displayName}」加入书架`);
    if (cacheError) toast(`后台缓存失败：${cacheError.message}`, 'error', 6000);
  } catch (error) { toast(error.message, 'error'); }
}

async function downloadQueryResult() {
  if (!state.queryMetadata) return;
  const button = $('[data-action="download-query-result"]');
  setBusy(button, true, '加入中…');
  try {
    const body = { plate: state.queryMetadata.queryId || state.queryMetadata.sourceId };
    if (!state.settings.downloadPath) {
      const selected = await api('/api/dialog/folder', { method: 'POST' });
      if (!selected.path) return;
      body.outputPath = selected.path;
    }
    body.title = state.queryMetadata.seriesTitle || state.queryMetadata.title || '';
    const response = await api('/api/downloads', { method: 'POST', body });
    closeModal('query-dialog');
    await loadDownloads();
    const anchor = $('#download-queue-anchor');
    anchor.classList.add('is-peeking');
    setTimeout(() => anchor.classList.remove('is-peeking'), 2600);
    toast(response.created ? '已加入下载队列，可以继续查找其他本子' : '相同任务已在下载队列中');
  } catch (error) { toast(error.message, 'error', 6000); }
  finally { setBusy(button, false); }
}

async function downloadFavorite() {
  const comic = state.activeComic;
  if (!isRemoteComic(comic)) return;
  const button = $('#detail-favorite-download');
  setBusy(button, true, '加入中…');
  try {
    const body = {
      plate: comic.favoriteAlbumId || comic.sourceId,
      title: comic.displayName || comic.title || '',
    };
    if (!state.settings.downloadPath) {
      const selected = await api('/api/dialog/folder', { method: 'POST' });
      if (!selected.path) return;
      body.outputPath = selected.path;
    }
    const response = await api('/api/downloads', { method: 'POST', body });
    closeDetail();
    await loadDownloads({ notify: false });
    const anchor = $('#download-queue-anchor');
    anchor.classList.add('is-peeking');
    setTimeout(() => anchor.classList.remove('is-peeking'), 2600);
    toast(response.created ? '已加入下载队列' : '这个作品已经在下载队列中');
  } catch (error) {
    toast(`加入下载失败：${error.message}`, 'error', 6000);
  } finally {
    setBusy(button, false);
  }
}

async function addFavoriteToShelf(comic = state.activeComic) {
  if (!isRemoteComic(comic)) return;
  const button = $('#detail-favorite-add');
  const albumId = comic.favoriteAlbumId || comic.sourceId;
  setBusy(button, true, '加入中…');
  try {
    const shelfUrl = comic.remoteOnline
      ? `/api/online/${encodeURIComponent(albumId)}/shelf`
      : `/api/provider/favorites/${encodeURIComponent(albumId)}/shelf`;
    const response = await api(shelfUrl, { method: 'POST' });
    if (state.activeComic?.id === comic.id) closeDetail();
    await Promise.all([loadLibrary(), loadSeries(), loadCaches({ notify: false })]);
    if (comic.remoteOnline) updateOnlineShelfState(albumId);
    const count = Number(response.comicCount || 1);
    toast(response.createdCount
      ? (count > 1 ? `已将 ${count} 话加入书架，正在缓存` : '已加入书架，正在缓存')
      : '这个收藏已经在书架或缓存队列中');
  } catch (error) {
    toast(`加入书架失败：${error.message}`, 'error', 6000);
  } finally {
    setBusy(button, false);
  }
}

async function saveDetail(event) {
  event.preventDefault();
  if (!state.activeComic) return;
  const submit = $('#detail-form button[type="submit"]');
  setBusy(submit, true, '保存中…');
  try {
    const collectionIds = $$('#detail-collections input:checked').map((input) => input.value);
    await api(`/api/comics/${state.activeComic.id}`, { method: 'PATCH', body: {
      nickname: $('#detail-nickname').value.trim(),
      note: $('#detail-note').value.trim(),
      coverPrivacyEnabled: $('#detail-cover-privacy-enabled').checked,
      coverPrivacyDirection: $('#detail-cover-privacy-direction').value,
      coverPrivacyStart: Number($('#detail-cover-privacy-start').value)
    } });
    await api(`/api/comics/${state.activeComic.id}/collections`, { method: 'PUT', body: { collectionIds } });
    closeDetail();
    await Promise.all([loadLibrary(), loadCollections(), loadSeries()]);
    toast('修改已保存');
  } catch (error) { toast(error.message, 'error'); }
  finally { setBusy(submit, false); }
}

async function chooseComicCover() {
  const comic = state.activeComic;
  if (!comic) return;
  try {
    const selected = await api('/api/dialog/image', { method: 'POST' });
    if (!selected.path) return;
    const updated = await api(`/api/comics/${comic.id}`, {
      method: 'PATCH',
      body: { coverPath: selected.path }
    });
    Object.assign(comic, updated);
    updateDetailPrivacyPreview();
    await Promise.all([loadLibrary(), loadSeries()]);
    toast('封面已更新');
  } catch (error) { toast(error.message, 'error', 5200); }
}

async function rematchComic() {
  const comic = state.activeComic;
  if (!comic?.sourceId) return toast('这个条目没有可匹配的车牌', 'error');
  const button = $('[data-action="rematch"]');
  setBusy(button, true, '更新中…');
  try {
    const updated = await api('/api/source/lookup', { method: 'POST', body: { plate: comic.sourceId, comicId: comic.id } });
    await Promise.all([loadLibrary(), loadSeries()]);
    closeDetail();
    showDetail(updated.id);
    toast('元数据已更新');
  } catch (error) { toast(error.message, 'error', 5200); }
  finally { setBusy(button, false); }
}

async function removeComic(comic = state.activeComic) {
  if (!comic) return;
  const decision = await requestConfirmation({
    title: '移出这部本子？',
    message: `可以只将「${comic.displayName}」移出书架并保留本地图片；也可以同时永久删除图片文件。`,
    confirmLabel: '仅移出书架',
    confirmTone: 'primary',
    alternativeLabel: '删除本子与文件'
  });
  if (!decision) return;
  const deleteFiles = decision === 'alternative';
  try {
    await api(`/api/comics/${comic.id}?deleteFiles=${deleteFiles}`, { method: 'DELETE' });
    if (state.activeComic?.id === comic.id) closeDetail();
    await Promise.all([loadLibrary(), loadCollections(), loadSeries()]);
    toast(deleteFiles ? '本子及本地文件已删除' : '已移出书架，本地文件保持不变');
  } catch (error) { toast(error.message, 'error'); }
}

function readerSeriesForComic(comic) {
  return comic ? state.series.find((series) => series.memberIds.includes(comic.id)) || null : null;
}

function readerChapterState() {
  const series = state.readerSeries;
  const index = series && state.readerComic ? series.memberIds.indexOf(state.readerComic.id) : -1;
  return {
    index,
    previous: index > 0 ? series.members[index - 1] : null,
    next: series && index >= 0 && index < series.members.length - 1 ? series.members[index + 1] : null,
  };
}

function renderReaderDirectory() {
  const chapters = state.readerSeries?.members || (state.readerComic ? [state.readerComic] : []);
  $('#reader-directory-title').textContent = state.readerSeries?.displayName || state.readerComic?.displayName || '章节目录';
  $('#reader-directory-list').innerHTML = chapters.map((comic, index) => `<button type="button" class="reader-directory-item ${comic.id === state.readerComic?.id ? 'is-current' : ''}" data-action="select-reader-chapter" data-comic-id="${comic.id}" style="--reveal-index:${index}">
    <span>${String(index + 1).padStart(2, '0')}</span><span><strong>${escapeHtml(comic.displayName)}</strong><small>${comic.pageCount || 0} 页${comic.sourceId ? ` · JM${escapeHtml(comic.sourceId)}` : ''}</small></span>${comic.id === state.readerComic?.id ? icon('play') : ''}
  </button>`).join('');
}

function setReaderDirectory(open) {
  const directory = $('#reader-directory');
  directory.classList.toggle('is-open', open);
  directory.setAttribute('aria-hidden', String(!open));
  $('#reader-directory-scrim').classList.toggle('hidden', !open);
  $('.reader-directory-toggle').classList.toggle('is-active', open);
}

function clearReaderPrefetch() {
  state.readerPrefetchLinks.forEach((link) => link.remove());
  state.readerPrefetchLinks = [];
}

async function preloadNextReaderChapter() {
  clearReaderPrefetch();
  const currentComicId = state.readerComic?.id;
  const next = readerChapterState().next;
  if (!next) return;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (state.readerComic?.id !== currentComicId || $('#reader').classList.contains('hidden')) return;
    const pages = await api(`/api/comics/${next.id}/pages`).catch(() => []);
    if (state.readerComic?.id !== currentComicId || $('#reader').classList.contains('hidden')) return;
    if (pages.length) {
      state.readerPrefetchLinks = pages.slice(0, 4).map((page) => {
        const link = document.createElement('link');
        link.rel = 'prefetch';
        link.as = 'image';
        link.href = `/media/page/${next.id}/${page.index}`;
        document.head.append(link);
        return link;
      });
      return;
    }
    await readerDelay(1000);
  }
}

function updateReaderChapterNavigation() {
  const chapter = readerChapterState();
  $('#reader-chapter-prev').disabled = !chapter.previous;
  $('#reader-chapter-next').disabled = !chapter.next;
  $('#reader-chapter-prev').title = chapter.previous ? `上一话：${chapter.previous.displayName}` : '已经是第一话';
  $('#reader-chapter-next').title = chapter.next ? `下一话：${chapter.next.displayName}` : '已经是最后一话';
  $('.reader-chapter-nav').classList.toggle('hidden', !state.readerSeries);
}

function readerDelay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function stopReaderLoadingMotion() {
  clearInterval(state.readerLoadingTimer);
  state.readerLoadingTimer = null;
}

function updateReaderLoadingMessage(message) {
  const title = $('#reader-loading-title');
  if (title && message) title.textContent = message;
}

function showReaderLoading(comic, message, { preview = false } = {}) {
  stopReaderLoadingMotion();
  closeDetail();
  state.readerPendingComic = comic;
  state.readerComic = null;
  state.readerSeries = null;
  state.readerPages = [];
  const reader = $('#reader');
  reader.classList.remove('hidden', 'page-mode', 'book-mode');
  reader.classList.add('is-loading');
  reader.setAttribute('aria-hidden', 'false');
  $('#reader-title').textContent = comic.displayName;
  $('#reader-progress').textContent = '准备中';
  $('#reader-pages').innerHTML = `<div class="reader-loading-state ${preview ? 'is-preview' : ''}">
    <div class="reader-loading-visual" aria-hidden="true"><span class="loading-sheet sheet-one"></span><span class="loading-sheet sheet-two"></span><span class="loading-route"><i></i><i></i><i></i></span></div>
    <strong id="reader-loading-title">${escapeHtml(message)}</strong>
    <span class="reader-loading-steps" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
  </div>`;
  const messages = preview
    ? ['正在选取最优链路…', '正在加载章节信息…', '正在加载首批四页…']
    : [message, '正在加载前几页…', '正在加载后续页面…'];
  let index = 0;
  state.readerLoadingTimer = setInterval(() => {
    if (!$('#reader').classList.contains('is-loading')) return stopReaderLoadingMotion();
    index = (index + 1) % messages.length;
    updateReaderLoadingMessage(messages[index]);
  }, 1100);
  $('.reader-chapter-nav').classList.add('hidden');
  document.body.classList.add('reader-open');
  document.body.style.overflow = 'hidden';
  setReaderDirectory(false);
}

function showReaderLoadError(message) {
  stopReaderLoadingMotion();
  $('#reader-pages').innerHTML = `<div class="reader-loading-state is-error"><strong>暂时无法打开</strong><small>${escapeHtml(message)}</small><button type="button" class="button primary" data-action="retry-reader-pending">重新加载</button></div>`;
}

function readerPageTemplate(comic, page, preview = false) {
  const source = preview ? page.url : `/media/page/${comic.id}/${page.index}`;
  const eager = Math.abs(page.index - state.readerPageIndex) < 3 ? 'eager' : 'lazy';
  return `<div class="reader-page" data-page="${page.index}"><span class="page-number">${page.index + 1}</span><img src="${source}" loading="${eager}" alt="第 ${page.index + 1} 页"></div>`;
}

function readerStreamingSuffix(available = state.readerPages.length) {
  const expected = Math.max(0, Number(state.readerComic?.pageCount || 0));
  return expected > available ? ` · 已载 ${available}/${expected}` : '';
}

function appendReaderPages(comic, pages, { preview = state.readerPreview } = {}) {
  const known = new Set(state.readerPages.map((page) => Number(page.index)));
  const additions = pages.filter((page) => !known.has(Number(page.index)));
  state.readerPages = pages;
  if (additions.length) {
    const streamState = $('#reader-stream-state');
    const html = additions.map((page) => readerPageTemplate(comic, page, preview)).join('');
    if (streamState) streamState.insertAdjacentHTML('beforebegin', html);
    else $('#reader-pages').insertAdjacentHTML('beforeend', html);
    additions.forEach((page) => {
      const image = $(`.reader-page[data-page="${page.index}"] img`);
      image?.addEventListener('load', () => image.classList.add('loaded'), { once: true });
    });
  }
  if (state.readerMode === 'scroll') {
    observeReaderPages(state.readerPages.length);
    $('#reader-progress').textContent = `${state.readerPageIndex + 1} / ${state.readerPages.length}${readerStreamingSuffix()}`;
  } else {
    updatePagedReader(false);
  }
}

function previewStreamMessage(response) {
  if (response.phase) return response.phase;
  const group = Math.floor(Number(response.availablePages || 0) / 4) % 3;
  return [
    '下一组四页正在接力抵达…',
    '后台正在填充后续页面…',
    '保持阅读，后页会持续跟上…',
  ][group];
}

function updatePreviewStreamState(response) {
  const available = Number(response.availablePages || 0);
  const expected = Number(response.pageCount || 0);
  let streamState = $('#reader-stream-state');
  if (response.status === 'ready' || expected && available >= expected) {
    streamState?.classList.add('is-complete');
    setTimeout(() => streamState?.remove(), 360);
    return;
  }
  if (!streamState) {
    $('#reader-pages').insertAdjacentHTML('beforeend', '<div id="reader-stream-state" class="reader-stream-state"><span class="stream-orbit" aria-hidden="true"><i></i><i></i><i></i></span><div><strong></strong><small></small></div></div>');
    streamState = $('#reader-stream-state');
  }
  $('strong', streamState).textContent = previewStreamMessage(response);
  $('small', streamState).textContent = expected ? `已准备 ${available} / ${expected} 页 · 每四页一组送达` : `已准备 ${available} 页 · 每四页一组送达`;
  streamState.classList.toggle('is-switching', Boolean(response.phase?.includes('切换')));
}

function startPreviewStreamUpdates(comic, albumId) {
  clearTimeout(state.readerStreamTimer);
  let interval = 240;
  const poll = async () => {
    if (!state.readerPreview || state.readerPreviewAlbumId !== albumId || $('#reader').classList.contains('hidden')) return;
    const response = await api(`/api/online/${encodeURIComponent(albumId)}/preview`).catch(() => null);
    if (!response || state.readerPreviewAlbumId !== albumId) return;
    if (response.status === 'failed') {
      toast(`预览后续页面加载失败：${response.error || '未知错误'}`, 'error', 6200);
      return;
    }
    comic.pageCount = Math.max(Number(comic.pageCount || 0), Number(response.pageCount || 0));
    if ((response.pages || []).length >= state.readerPages.length) {
      if ((response.pages || []).length > state.readerPages.length) interval = 240;
      appendReaderPages(comic, response.pages || [], { preview: true });
    }
    updatePreviewStreamState(response);
    if (response.status !== 'ready' || state.readerPages.length < Number(response.pageCount || 0)) {
      interval = Math.min(500, interval + 40);
      state.readerStreamTimer = setTimeout(poll, interval);
    }
  };
  state.readerStreamTimer = setTimeout(poll, 220);
}

function startReaderStreamUpdates(comic) {
  clearTimeout(state.readerStreamTimer);
  if (state.readerPreview || !comic?.sourceId) return;
  const expected = Math.max(0, Number(comic.pageCount || 0));
  if (expected && state.readerPages.length >= expected) return;
  const comicId = comic.id;
  let idlePolls = 0;
  const poll = async () => {
    if (state.readerPreview || state.readerComic?.id !== comicId || $('#reader').classList.contains('hidden')) return;
    const pages = await api(`/api/comics/${comicId}/pages`).catch(() => []);
    if (state.readerComic?.id !== comicId) return;
    const previousCount = state.readerPages.length;
    if (pages.length >= previousCount) appendReaderPages(comic, pages);
    const total = Math.max(0, Number(comic.pageCount || 0));
    if (!total || state.readerPages.length < total) {
      const active = activeCacheTaskForComic(comic);
      idlePolls = pages.length > previousCount || active ? 0 : idlePolls + 1;
      if (idlePolls >= 4) {
        toast('后续页面缓存已暂停，可退出后再次点击继续', 'error', 5200);
        return;
      }
      state.readerStreamTimer = setTimeout(poll, 850);
    }
  };
  state.readerStreamTimer = setTimeout(poll, 650);
}

function activateReader(comic, pages, { preview = false } = {}) {
  stopReaderLoadingMotion();
  clearTimeout(state.readerStreamTimer);
  state.readerPendingComic = null;
  state.readerPreview = preview;
  setActivityFocus(preview ? 'preview' : 'library');
  state.readerComic = comic;
  state.readerSeries = preview ? null : readerSeriesForComic(comic);
  state.readerPages = pages;
  state.readerPageIndex = Math.max(0, Math.min(Number(preview ? 0 : comic.progressPage || 0), pages.length - 1));
  state.readerMode = localStorage.getItem('jmshelf-reader-mode') || 'scroll';
  state.readerAutoMode = ['continuous', 'stepped'].includes(localStorage.getItem('jmshelf-reader-auto-mode'))
    ? localStorage.getItem('jmshelf-reader-auto-mode')
    : 'continuous';
  state.readerAutoPace = ['slow', 'normal', 'fast'].includes(localStorage.getItem('jmshelf-reader-auto-pace'))
    ? localStorage.getItem('jmshelf-reader-auto-pace')
    : 'normal';
  state.readerBookLeftForward = localStorage.getItem('jmshelf-reader-book-left-forward') === 'true';
  closeDetail();
  $('#reader-title').textContent = preview ? `${comic.displayName} · 在线预览` : comic.displayName;
  $('#reader-pages').innerHTML = pages.map((page) => readerPageTemplate(comic, page, preview)).join('');
  $('#reader').classList.remove('hidden', 'is-loading');
  $('#reader').setAttribute('aria-hidden', 'false');
  document.body.classList.add('reader-open');
  document.body.style.overflow = 'hidden';
  $$('#reader-pages img').forEach((image) => image.addEventListener('load', () => image.classList.add('loaded'), { once: true }));
  renderReaderDirectory();
  updateReaderChapterNavigation();
  setReaderDirectory(false);
  setReaderMode(state.readerMode, false);
  if (!preview) {
    preloadNextReaderChapter();
    startReaderStreamUpdates(comic);
  } else if (state.readerPreviewAlbumId) {
    updatePreviewStreamState({
      status: Number(comic.pageCount || 0) > 0 && pages.length >= Number(comic.pageCount || 0) ? 'ready' : 'loading',
      availablePages: pages.length,
      pageCount: comic.pageCount,
      phase: '首批页面已就绪，后续继续加载…',
    });
    startPreviewStreamUpdates(comic, state.readerPreviewAlbumId);
  }
}

async function waitForPrioritizedCache(comic, token, taskIds) {
  for (let attempt = 0; attempt < 900; attempt += 1) {
    if (token !== state.readerRequestToken || $('#reader').classList.contains('hidden')) return;
    const pages = await api(`/api/comics/${comic.id}/pages`).catch(() => []);
    if (pages.length) {
      const fresh = await api(`/api/comics/${comic.id}`).catch(() => comic);
      Object.assign(comic, fresh);
      api(`/api/comics/${comic.id}/read`, { method: 'POST' }).then((recorded) => {
        comic.readCount = recorded.readCount;
        comic.lastReadAt = recorded.lastReadAt;
      }).catch(() => {});
      if (token === state.readerRequestToken) activateReader(comic, pages);
      return;
    }
    const snapshot = await api('/api/caches').catch(() => null);
    const failed = (snapshot?.items || []).find((task) => taskIds.has(String(task.id)) && task.status === 'failed');
    if (failed) throw new Error(failed.error || '缓存失败');
    await readerDelay(850);
  }
  throw new Error('缓存等待时间过长，请稍后重试');
}

async function openReader(comic = state.activeComic) {
  if (!comic) return;
  stopReaderAutoScroll();
  clearTimeout(state.readerStreamTimer);
  const token = ++state.readerRequestToken;
  const previousWasPreview = state.readerPreview;
  const previousPreviewAlbumId = state.readerPreviewAlbumId;
  state.readerReturnDetailId = null;
  state.readerPreviewAlbumId = '';
  if (previousWasPreview && previousPreviewAlbumId) cancelOnlinePreview(previousPreviewAlbumId);
  try {
    if (state.readerComic && !previousWasPreview && state.readerComic.id !== comic.id) {
      clearTimeout(state.readerProgressTimer);
      state.readerComic.progressPage = state.readerPageIndex;
      state.readerComic.lastReadAt = new Date().toISOString();
      await api(`/api/comics/${state.readerComic.id}/progress`, {
        method: 'PUT',
        body: { page: state.readerPageIndex }
      }).catch(() => {});
    }
    state.readerPreview = false;
    const pages = await api(`/api/comics/${comic.id}/pages`);
    if (!pages.length) {
      if (comic.sourceId && (!comic.rootPath || comic.storageKind === 'cache' || comic.storageKind === 'remote')) {
        showReaderLoading(comic, '正在优先加载这一话…');
        const queued = await cacheComics([comic], { notify: false, priority: true });
        const taskIds = new Set((queued.tasks || []).map((task) => String(task.id)));
        await waitForPrioritizedCache(comic, token, taskIds);
        return;
      }
      return toast('尚未关联可阅读的本地图片', 'error');
    }
    if (comic.sourceId && Number(comic.pageCount || 0) > pages.length) {
      cacheComics([comic], { notify: false, priority: true }).catch(() => {});
    }
    api(`/api/comics/${comic.id}/read`, { method: 'POST' }).then((recorded) => {
      comic.readCount = recorded.readCount;
      comic.lastReadAt = recorded.lastReadAt;
    }).catch(() => {});
    if (token === state.readerRequestToken) activateReader(comic, pages);
  } catch (error) {
    if (token === state.readerRequestToken && !$('#reader').classList.contains('hidden')) showReaderLoadError(error.message);
    toast(error.message, 'error');
  }
}

async function previewOnlineComic(comic = state.activeComic, chapter = null) {
  if (!comic?.sourceId) return;
  const token = ++state.readerRequestToken;
  const albumId = String(comic.favoriteAlbumId || comic.sourceId);
  const chapterId = String(chapter?.sourceId || '');
  const readerComic = chapter ? {
    ...comic,
    displayName: chapter.title || `${comic.displayName} · 第 ${chapter.chapterIndex || ''} 话`,
    title: chapter.title || comic.title,
    previewChapterId: chapterId,
  } : comic;
  state.readerReturnDetailId = comic.id;
  state.readerPreview = true;
  state.readerPreviewAlbumId = albumId;
  showReaderLoading(readerComic, '正在选取最优链路…', { preview: true });
  try {
    let preview = await api(`/api/online/${encodeURIComponent(albumId)}/preview`, {
      method: 'POST',
      body: { chapterId },
    });
    let wait = 100;
    while (preview.status === 'loading' && !(preview.pages || []).length) {
      await readerDelay(wait);
      if (token !== state.readerRequestToken || $('#reader').classList.contains('hidden')) return;
      preview = await api(`/api/online/${encodeURIComponent(albumId)}/preview`);
      updateReaderLoadingMessage(preview.phase || '正在加载首批四页…');
      wait = Math.min(220, wait + 20);
    }
    if (preview.status === 'failed') throw new Error(preview.error || '在线预览加载失败');
    if (!(preview.pages || []).length) throw new Error('在线预览暂时没有可阅读页面');
    if (token !== state.readerRequestToken || $('#reader').classList.contains('hidden')) return;
    readerComic.pageCount = Number(preview.pageCount || readerComic.pageCount || 0);
    activateReader(readerComic, preview.pages || [], { preview: true });
  } catch (error) {
    if (token === state.readerRequestToken) showReaderLoadError(error.message);
    toast(`预览读取失败：${error.message}`, 'error', 6200);
  }
}

async function switchReaderChapter(directionOrId) {
  let target = null;
  if (typeof directionOrId === 'string') {
    target = state.readerSeries?.members.find((comic) => comic.id === directionOrId) || null;
  } else {
    const chapter = readerChapterState();
    target = directionOrId < 0 ? chapter.previous : chapter.next;
  }
  if (!target || target.id === state.readerComic?.id) return;
  setReaderDirectory(false);
  await openReader(target);
}

function saveReaderProgress(page, delay = 250) {
  if (!state.readerComic || state.readerPreview) return;
  state.readerComic.progressPage = page;
  state.readerComic.lastReadAt = new Date().toISOString();
  clearTimeout(state.readerProgressTimer);
  state.readerProgressTimer = setTimeout(() => {
    api(`/api/comics/${state.readerComic.id}/progress`, { method: 'PUT', body: { page } }).catch(() => {});
  }, delay);
}

function observeReaderPages(total) {
  state.readerObserver?.disconnect();
  state.readerObserver = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible || !state.readerComic) return;
    const page = Number(visible.target.dataset.page);
    state.readerPageIndex = page;
    $('#reader-progress').textContent = `${page + 1} / ${total}${readerStreamingSuffix(total)}`;
    saveReaderProgress(page, 600);
  }, { root: $('#reader'), threshold: [0.35, 0.65] });
  $$('.reader-page').forEach((page) => state.readerObserver.observe(page));
}

function updateReaderAutoControls() {
  const toggle = $('#reader-auto-toggle');
  const toggleIcon = $('.icon', toggle);
  $('#reader-auto-mode').value = state.readerAutoMode;
  $('#reader-auto-pace').value = state.readerAutoPace;
  $('#reader-auto-settings').classList.toggle('is-active', state.readerAutoSettingsOpen);
  $('#reader-auto-settings').setAttribute('aria-expanded', String(state.readerAutoSettingsOpen));
  $('#reader-auto-popover').classList.toggle('hidden', !state.readerAutoSettingsOpen);
  toggle.classList.toggle('is-active', state.readerAutoRunning);
  toggle.setAttribute('aria-pressed', String(state.readerAutoRunning));
  toggle.setAttribute('title', state.readerAutoRunning ? '暂停自动滑动' : '开始自动滑动');
  toggleIcon.classList.toggle('icon-play', !state.readerAutoRunning);
  toggleIcon.classList.toggle('icon-pause', state.readerAutoRunning);
  $('#reader-auto-label').textContent = state.readerAutoRunning ? '暂停' : '自动';
}

function stopReaderAutoScroll(notify = false) {
  const wasRunning = state.readerAutoRunning;
  state.readerAutoRunning = false;
  state.readerAutoLastFrame = 0;
  state.readerAutoPosition = null;
  if (state.readerAutoFrame !== null) cancelAnimationFrame(state.readerAutoFrame);
  clearTimeout(state.readerAutoTimer);
  state.readerAutoFrame = null;
  state.readerAutoTimer = null;
  updateReaderAutoControls();
  if (notify && wasRunning) toast('自动滑动已暂停');
}

function readerScrollLimit() {
  const reader = $('#reader');
  return Math.max(0, reader.scrollHeight - reader.clientHeight);
}

function finishReaderAutoScroll() {
  stopReaderAutoScroll();
  toast('已到本章末尾');
}

function runContinuousReaderAutoScroll(timestamp) {
  if (!state.readerAutoRunning || state.readerMode !== 'scroll' || state.readerAutoMode !== 'continuous') return;
  const reader = $('#reader');
  const limit = readerScrollLimit();
  if (reader.scrollTop >= limit - 1) return finishReaderAutoScroll();
  if (!state.readerAutoLastFrame) state.readerAutoLastFrame = timestamp;
  const elapsed = Math.min(100, timestamp - state.readerAutoLastFrame);
  state.readerAutoLastFrame = timestamp;
  if (state.readerAutoPosition === null) state.readerAutoPosition = reader.scrollTop;
  state.readerAutoPosition = Math.min(limit, state.readerAutoPosition + READER_AUTO_SPEEDS[state.readerAutoPace] * elapsed / 1000);
  reader.scrollTop = Math.floor(state.readerAutoPosition);
  state.readerAutoFrame = requestAnimationFrame(runContinuousReaderAutoScroll);
}

function scheduleSteppedReaderAutoScroll(delay = 350) {
  clearTimeout(state.readerAutoTimer);
  state.readerAutoTimer = setTimeout(() => {
    if (!state.readerAutoRunning || state.readerMode !== 'scroll' || state.readerAutoMode !== 'stepped') return;
    const reader = $('#reader');
    const limit = readerScrollLimit();
    if (reader.scrollTop >= limit - 1) return finishReaderAutoScroll();
    const distance = Math.max(240, Math.round(reader.clientHeight - 28));
    reader.scrollTo({ top: Math.min(limit, reader.scrollTop + distance), behavior: 'smooth' });
    scheduleSteppedReaderAutoScroll(READER_AUTO_PAUSES[state.readerAutoPace]);
  }, delay);
}

function startReaderAutoScroll() {
  if (state.readerMode !== 'scroll') return toast('自动滑动仅在连续滑动模式可用', 'error');
  if (readerScrollLimit() <= 1) return toast('当前页面无需滑动', 'error');
  state.readerAutoRunning = true;
  state.readerAutoLastFrame = 0;
  state.readerAutoPosition = $('#reader').scrollTop;
  updateReaderAutoControls();
  if (state.readerAutoMode === 'continuous') {
    state.readerAutoFrame = requestAnimationFrame(runContinuousReaderAutoScroll);
  } else {
    scheduleSteppedReaderAutoScroll();
  }
}

function toggleReaderAutoScroll() {
  if (state.readerAutoRunning) stopReaderAutoScroll();
  else startReaderAutoScroll();
}

function setReaderAutoSettings(open) {
  state.readerAutoSettingsOpen = Boolean(open);
  updateReaderAutoControls();
}

function restartReaderAutoScrollIfNeeded(update) {
  const wasRunning = state.readerAutoRunning;
  stopReaderAutoScroll();
  update();
  updateReaderAutoControls();
  if (wasRunning) startReaderAutoScroll();
}

function setReaderAutoMode(mode) {
  restartReaderAutoScrollIfNeeded(() => {
    state.readerAutoMode = mode === 'stepped' ? 'stepped' : 'continuous';
    localStorage.setItem('jmshelf-reader-auto-mode', state.readerAutoMode);
  });
}

function setReaderAutoPace(pace) {
  restartReaderAutoScrollIfNeeded(() => {
    state.readerAutoPace = ['slow', 'fast'].includes(pace) ? pace : 'normal';
    localStorage.setItem('jmshelf-reader-auto-pace', state.readerAutoPace);
  });
}

function updateReaderBookDirection() {
  const reverseActive = state.readerMode === 'book' && state.readerBookLeftForward;
  const button = $('#reader-book-direction');
  const buttonIcon = $('.icon', button);
  const previousIcon = $('.reader-prev .icon');
  const nextIcon = $('.reader-next .icon');
  $('#reader').classList.toggle('book-left-forward', reverseActive);
  button.setAttribute('aria-pressed', String(state.readerBookLeftForward));
  button.setAttribute('title', state.readerBookLeftForward ? '当前为左页前进，点击切换' : '当前为右页前进，点击切换');
  button.querySelector('span:last-child').textContent = state.readerBookLeftForward ? '左页前进' : '右页前进';
  buttonIcon.classList.toggle('icon-arrow-left', state.readerBookLeftForward);
  buttonIcon.classList.toggle('icon-arrow-right', !state.readerBookLeftForward);
  previousIcon.classList.toggle('icon-arrow-left', !reverseActive);
  previousIcon.classList.toggle('icon-arrow-right', reverseActive);
  nextIcon.classList.toggle('icon-arrow-right', !reverseActive);
  nextIcon.classList.toggle('icon-arrow-left', reverseActive);
}

function toggleReaderBookDirection() {
  state.readerBookLeftForward = !state.readerBookLeftForward;
  localStorage.setItem('jmshelf-reader-book-left-forward', String(state.readerBookLeftForward));
  updateReaderBookDirection();
  if (state.readerMode === 'book') updatePagedReader(false);
}

function updatePagedReader(save = true) {
  const total = state.readerPages.length;
  if (!total) return;
  const isBook = state.readerMode === 'book';
  if (isBook) state.readerPageIndex = Math.floor(state.readerPageIndex / 2) * 2;
  state.readerPageIndex = Math.max(0, Math.min(state.readerPageIndex, total - 1));
  const secondPage = isBook && state.readerPageIndex + 1 < total ? state.readerPageIndex + 1 : null;
  $$('.reader-page').forEach((page) => page.classList.remove('is-current', 'spread-left', 'spread-right'));
  const first = $(`.reader-page[data-page="${state.readerPageIndex}"]`);
  const firstSide = state.readerBookLeftForward ? 'spread-right' : 'spread-left';
  const secondSide = state.readerBookLeftForward ? 'spread-left' : 'spread-right';
  first?.classList.add('is-current', ...(isBook ? [firstSide] : []));
  if (secondPage !== null) $(`.reader-page[data-page="${secondPage}"]`)?.classList.add('is-current', secondSide);
  $('#reader-progress').textContent = secondPage === null
    ? `${state.readerPageIndex + 1} / ${total}${readerStreamingSuffix(total)}`
    : `${state.readerPageIndex + 1}–${secondPage + 1} / ${total}${readerStreamingSuffix(total)}`;
  $('.reader-prev').disabled = state.readerPageIndex <= 0;
  $('.reader-next').disabled = isBook ? state.readerPageIndex + 2 >= total : state.readerPageIndex + 1 >= total;
  if (save) saveReaderProgress(state.readerPageIndex);
}

function setReaderMode(mode, savePreference = true) {
  stopReaderAutoScroll();
  state.readerMode = ['scroll', 'page', 'book'].includes(mode) ? mode : 'scroll';
  if (savePreference) localStorage.setItem('jmshelf-reader-mode', state.readerMode);
  $$('.reader-mode-button').forEach((button) => {
    const active = button.dataset.readerMode === state.readerMode;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('#reader').classList.toggle('page-mode', state.readerMode === 'page');
  $('#reader').classList.toggle('book-mode', state.readerMode === 'book');
  $('#reader-width-control').classList.toggle('hidden', state.readerMode !== 'scroll');
  $('#reader-auto-controls').classList.toggle('hidden', state.readerMode !== 'scroll');
  $('#reader-book-direction').classList.toggle('hidden', state.readerMode !== 'book');
  updateReaderBookDirection();
  $('[data-action="reader-top"] span:last-child').textContent = state.readerMode === 'scroll' ? '回到顶部' : '第一页';
  state.readerObserver?.disconnect();
  if (state.readerMode === 'scroll') {
    $$('.reader-page').forEach((page) => page.classList.remove('is-current', 'spread-left', 'spread-right'));
    observeReaderPages(state.readerPages.length);
    requestAnimationFrame(() => {
      const target = $(`.reader-page[data-page="${state.readerPageIndex}"]`);
      if (target && state.readerPageIndex > 0) target.scrollIntoView({ block: 'start' });
      else $('#reader').scrollTop = 0;
    });
  } else {
    $('#reader').scrollTop = 0;
    updatePagedReader(false);
  }
}

function turnReaderPage(direction) {
  if (state.readerMode === 'scroll') return;
  const step = state.readerMode === 'book' ? 2 : 1;
  const previousPage = state.readerPageIndex;
  state.readerPageIndex += direction * step;
  updatePagedReader();
  if (state.readerPageIndex === previousPage) {
    if (direction > 0 && readerChapterState().next) switchReaderChapter(1);
    else if (direction < 0 && readerChapterState().previous) switchReaderChapter(-1);
    return;
  }
  const reader = $('#reader');
  clearTimeout(state.readerTurnTimer);
  reader.classList.remove('turn-forward', 'turn-backward');
  void reader.offsetWidth;
  const visuallyForward = state.readerMode === 'book' && state.readerBookLeftForward ? direction < 0 : direction > 0;
  reader.classList.add(visuallyForward ? 'turn-forward' : 'turn-backward');
  state.readerTurnTimer = setTimeout(() => reader.classList.remove('turn-forward', 'turn-backward'), 380);
}

function readerToFirstPage() {
  if (state.readerMode === 'scroll') $('#reader').scrollTo({ top: 0, behavior: 'smooth' });
  else {
    state.readerPageIndex = 0;
    updatePagedReader();
  }
}

function closeReader() {
  const returnDetailId = state.readerReturnDetailId;
  const previewAlbumId = state.readerPreview ? state.readerPreviewAlbumId : '';
  state.readerReturnDetailId = null;
  state.readerRequestToken += 1;
  stopReaderAutoScroll();
  state.readerObserver?.disconnect();
  clearTimeout(state.readerProgressTimer);
  clearTimeout(state.readerStreamTimer);
  clearTimeout(state.readerTurnTimer);
  stopReaderLoadingMotion();
  if (state.readerComic && !state.readerPreview) {
    const comic = state.readerComic;
    comic.progressPage = state.readerPageIndex;
    comic.lastReadAt = new Date().toISOString();
    api(`/api/comics/${comic.id}/progress`, { method: 'PUT', body: { page: state.readerPageIndex } })
      .catch(() => {})
      .finally(() => Promise.all([loadLibrary(), loadSeries()]).catch(() => {}));
  }
  clearReaderPrefetch();
  setReaderDirectory(false);
  $('#reader').classList.remove('turn-forward', 'turn-backward', 'is-loading');
  $('#reader').classList.add('hidden');
  $('#reader').setAttribute('aria-hidden', 'true');
  document.body.classList.remove('reader-open');
  document.body.style.overflow = '';
  state.readerComic = null;
  state.readerPendingComic = null;
  state.readerPreview = false;
  state.readerPreviewAlbumId = '';
  state.readerSeries = null;
  state.readerPages = [];
  if (previewAlbumId) cancelOnlinePreview(previewAlbumId);
  setActivityFocus(state.activeCollection === 'jm-online' ? 'online' : 'library');
  if (returnDetailId) requestAnimationFrame(() => showDetail(returnDetailId));
}

function panicHideApp() {
  if (panicHideInFlight) return;
  panicHideInFlight = true;
  if (state.readerComic && !state.readerPreview) {
    clearTimeout(state.readerProgressTimer);
    state.readerComic.progressPage = state.readerPageIndex;
    state.readerComic.lastReadAt = new Date().toISOString();
    api(`/api/comics/${state.readerComic.id}/progress`, { method: 'PUT', body: { page: state.readerPageIndex } }).catch(() => {});
  }
  api('/api/window/to-tray', { method: 'POST' })
    .catch((error) => toast(error.message, 'error'))
    .finally(() => setTimeout(() => { panicHideInFlight = false; }, 450));
}

function renderSeriesBuilder() {
  const selectedSet = new Set(state.seriesSelection);
  const byId = new Map(state.seriesCandidateComics.map((comic) => [comic.id, comic]));
  $('#series-selected-count').textContent = `${state.seriesSelection.length} 本`;
  $('#series-selected-list').innerHTML = state.seriesSelection.length
    ? state.seriesSelection.map((comicId, index) => {
      const comic = byId.get(comicId);
      if (!comic) return '';
      return `<div class="series-selected-item" style="--reveal-index:${index}">
        <span class="series-order">${index + 1}</span>
        <span class="series-mini-cover">${comic.coverPath ? `<img src="${comicCoverUrl(comic)}" alt="">` : ''}${coverPrivacyMask(comic)}</span>
        <strong>${escapeHtml(comic.displayName)}</strong>
        <span class="series-order-actions">
          <button type="button" data-action="move-series-selection" data-comic-id="${comic.id}" data-direction="-1" ${index === 0 ? 'disabled' : ''} aria-label="上移">${icon('arrow-up')}</button>
          <button type="button" data-action="move-series-selection" data-comic-id="${comic.id}" data-direction="1" ${index === state.seriesSelection.length - 1 ? 'disabled' : ''} aria-label="下移">${icon('arrow-down')}</button>
          <button type="button" data-action="remove-series-selection" data-comic-id="${comic.id}" aria-label="移除">${icon('close')}</button>
        </span>
      </div>`;
    }).join('')
    : '<p class="series-empty">按顺序从下方选择本子</p>';
  $('#series-candidate-list').innerHTML = state.seriesCandidateComics.length
    ? state.seriesCandidateComics.map((comic, index) => `<button type="button" class="series-candidate ${selectedSet.has(comic.id) ? 'is-selected' : ''}" data-action="select-series-comic" data-comic-id="${comic.id}" style="--reveal-index:${index}" ${selectedSet.has(comic.id) ? 'disabled' : ''}>
      <span class="series-mini-cover">${comic.coverPath ? `<img src="${comicCoverUrl(comic)}" alt="">` : ''}${coverPrivacyMask(comic)}</span>
      <span><strong>${escapeHtml(comic.displayName)}</strong><small>${comic.sourceId ? `JM${escapeHtml(comic.sourceId)} · ` : ''}${comic.pageCount || 0} 页</small></span>
      <b>${selectedSet.has(comic.id) ? state.seriesSelection.indexOf(comic.id) + 1 : icon('plus')}</b>
    </button>`).join('')
    : '<p class="series-empty">没有可加入系列的独立本子</p>';
  $('#series-builder-form button[type="submit"]').disabled = state.seriesSelection.length < 2;
}

async function openSeriesBuilder(preselectedComicId = null) {
  const button = $('#add-series');
  setBusy(button, true, '载入中…');
  try {
    const allComics = await api('/api/comics');
    const usedIds = new Set(state.series.flatMap((series) => series.memberIds));
    state.seriesCandidateComics = allComics.filter((comic) => !usedIds.has(comic.id));
    state.seriesSelection = preselectedComicId && state.seriesCandidateComics.some((comic) => comic.id === preselectedComicId)
      ? [preselectedComicId]
      : [];
    $('#series-builder-status').classList.add('hidden');
    renderSeriesBuilder();
    openModal('series-builder-dialog');
  } catch (error) { toast(error.message, 'error'); }
  finally { setBusy(button, false); }
}

function selectSeriesComic(comicId) {
  if (!state.seriesSelection.includes(comicId)) state.seriesSelection.push(comicId);
  renderSeriesBuilder();
}

function removeSeriesSelection(comicId) {
  state.seriesSelection = state.seriesSelection.filter((id) => id !== comicId);
  renderSeriesBuilder();
}

function moveSeriesSelection(comicId, direction) {
  const index = state.seriesSelection.indexOf(comicId);
  const target = index + Number(direction);
  if (index < 0 || target < 0 || target >= state.seriesSelection.length) return;
  [state.seriesSelection[index], state.seriesSelection[target]] = [state.seriesSelection[target], state.seriesSelection[index]];
  renderSeriesBuilder();
}

async function createSeries(event) {
  event.preventDefault();
  if (state.seriesSelection.length < 2) return;
  const submit = $('#series-builder-form button[type="submit"]');
  setBusy(submit, true, '创建中…');
  try {
    const created = await api('/api/library-series', { method: 'POST', body: { comicIds: state.seriesSelection } });
    await loadSeries();
    closeModal('series-builder-dialog');
    toast(`系列「${created.displayName}」已创建`);
  } catch (error) {
    $('#series-builder-status').textContent = error.message;
    $('#series-builder-status').classList.remove('hidden');
  } finally { setBusy(submit, false); }
}

function showSeries(seriesId, { preserveScroll = false } = {}) {
  const series = state.series.find((item) => item.id === seriesId);
  if (!series) return;
  const isSourceSeries = series.kind === 'source' || series.isSourceSeries;
  const chapterScroll = $('#series-chapter-scroll');
  const scrollTop = preserveScroll && state.activeSeries?.id === seriesId ? chapterScroll.scrollTop : 0;
  state.activeSeries = series;
  const resume = seriesResumeComic(series);
  const latest = series.members.at(-1);
  $('#series-title').textContent = series.displayName;
  $('#series-title').title = series.displayName;
  $('.series-view-card > .eyebrow').textContent = isSourceSeries ? 'JM MULTI-P' : 'SERIES';
  $('.series-view-card').classList.toggle('is-source-series', isSourceSeries);
  const updateCount = Number(series.updateAvailableCount || 0);
  const checkLabel = seriesCheckLabel(series.lastCheckedAt);
  $('#series-subtitle').textContent = isSourceSeries
    ? `${series.members.length} P · 点击右侧分 P 直接阅读`
    : (resume ? seriesResumeLabel(series, resume) : '选择一本查看详情');
  const updateStatus = $('#series-update-status');
  updateStatus.title = series.updateError || '';
  updateStatus.classList.toggle('hidden', !series.updateSupported);
  updateStatus.classList.toggle('has-update', updateCount > 0);
  updateStatus.classList.toggle('has-error', Boolean(series.updateError));
  if (series.updateSupported) {
    if (updateCount) {
      updateStatus.innerHTML = `${icon('refresh')}<span><strong>发现 ${updateCount} 话更新</strong><small>${escapeHtml(checkLabel)} · 点击更新后会下载并自动加入本系列</small></span>`;
    } else if (series.updateError) {
      updateStatus.innerHTML = `${icon('close')}<span><strong>上次检查失败</strong><small>${escapeHtml(series.updateError)}</small></span>`;
    } else {
      updateStatus.innerHTML = `${icon('tick')}<span><strong>启动及每日检查</strong><small>${escapeHtml(checkLabel)} · 当前没有发现新话</small></span>`;
    }
  }
  if (isSourceSeries && series.updateSupported && latest) {
    updateStatus.insertAdjacentHTML('beforeend', `<span class="series-latest-info"><small>最新话</small><strong>P${escapeHtml(latest.chapterIndex || series.members.length)} · ${escapeHtml(latest.displayName)}</strong></span>`);
  }
  const refreshButton = $('#series-refresh');
  refreshButton.classList.toggle('hidden', !series.updateSupported);
  refreshButton.dataset.seriesId = series.id;
  const refreshLabel = refreshButton.querySelector('span:last-child');
  if (refreshLabel) refreshLabel.textContent = updateCount ? `更新 ${updateCount} 话` : '检查更新';
  const continueButton = $('#series-continue-reading');
  continueButton.dataset.seriesId = series.id;
  continueButton.disabled = !resume;
  continueButton.querySelector('span:last-child').textContent = resume?.lastReadAt ? '继续上次阅读' : '从第一话开始';
  const dissolveButton = $('[data-action="dissolve-series"]');
  dissolveButton.classList.remove('hidden');
  continueButton.classList.remove('hidden');
  dissolveButton.querySelector('span:last-child').textContent = isSourceSeries ? '移除书库' : '解除系列';
  const bookList = $('#series-book-list');
  bookList.classList.toggle('is-source-series', isSourceSeries);
  if (isSourceSeries) {
    const cover = series.members[0];
    const author = cover?.authors?.join(' · ') || '未知作者';
    const tags = (cover?.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('');
    bookList.innerHTML = `<div class="source-series-hero">
      <div class="source-series-hero-cover">${cover?.coverPath ? `<img src="${comicCoverUrl(cover)}" alt="${escapeHtml(series.displayName)} 的默认封面" loading="eager" decoding="async">` : '<span class="series-book-cover-empty"></span>'}${coverPrivacyMask(cover)}<span>${series.members.length} P</span></div>
      <div class="source-series-hero-info"><strong>JM${escapeHtml(series.sourceSeriesId || cover?.seriesId || cover?.sourceId || '')}</strong><span>${escapeHtml(author)} · ${series.members.length} P</span>${tags ? `<div>${tags}</div>` : ''}</div>
    </div><div class="source-series-chapter-list">${series.members.map((comic, index) => `<button type="button" class="source-series-chapter" data-action="read-source-series-comic" data-comic-id="${comic.id}" style="--reveal-index:${index}">
      <b>P${escapeHtml(comic.chapterIndex || index + 1)}</b><span><strong>${escapeHtml(comic.displayName)}</strong><small>${comic.sourceId ? `JM${escapeHtml(comic.sourceId)} · ` : ''}${comic.pageCount || 0} 页${comic.lastReadAt ? ` · 读到 ${Number(comic.progressPage || 0) + 1} 页` : ''}</small></span>${icon(comic.lastReadAt ? 'bookmark' : 'arrow-right')}
    </button>`).join('')}</div>`;
  } else {
    bookList.innerHTML = series.members.map((comic, index) => `<button type="button" class="series-book-entry" data-action="show-series-comic-detail" data-comic-id="${comic.id}" style="--reveal-index:${index}">
      <span class="series-book-cover">${comic.coverPath ? `<img src="${comicCoverUrl(comic)}" alt="${escapeHtml(comic.displayName)} 的封面" loading="lazy" decoding="async">` : '<span class="series-book-cover-empty"></span>'}${coverPrivacyMask(comic)}<i>${index + 1}</i></span>
      <span class="series-book-copy"><strong>${escapeHtml(comic.displayName)}</strong><small>${comic.sourceId ? `JM${escapeHtml(comic.sourceId)} · ` : ''}${comic.pageCount || 0} 页</small></span>
    </button>`).join('');
  }
  openModal('series-dialog');
  chapterScroll.scrollTop = scrollTop;
}

function onlineSeriesChapters(comic) {
  return [...(comic?.episodeManifest || [])].sort((left, right) => (
    Number(left.chapterIndex || 0) - Number(right.chapterIndex || 0)
    || String(left.sourceId || '').localeCompare(String(right.sourceId || ''))
  ));
}

function showOnlineSeries(comic = state.activeComic) {
  if (!comic?.remoteOnline || !comic.episodeManifest?.length) return;
  const chapters = onlineSeriesChapters(comic);
  const latest = chapters.at(-1);
  closeDetail();
  state.activeSeries = { id: `online:${comic.sourceId}`, remoteOnline: true, parentComicId: comic.id, members: chapters };
  $('.series-view-card').classList.add('is-source-series');
  $('.series-view-card > .eyebrow').textContent = '禁漫书库';
  $('#series-title').textContent = comic.displayName;
  $('#series-title').title = comic.displayName;
  $('#series-subtitle').textContent = `${chapters.length} 话 · 选择后再开始加载`;
  $('#series-update-status').classList.add('hidden');
  $('#series-refresh').classList.add('hidden');
  $('#series-continue-reading').classList.add('hidden');
  $('[data-action="dissolve-series"]').classList.add('hidden');
  const author = comic.authors?.join(' · ') || '未知作者';
  const tags = (comic.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('');
  const coverSource = comic.directCoverUrl || comic.coverUrl;
  const chapterButton = (chapter, index, pinned = false) => `<button type="button" class="source-series-chapter ${pinned ? 'is-latest-online' : ''}" data-action="preview-online-chapter" data-online-album-id="${comic.sourceId}" data-online-chapter-id="${chapter.sourceId}" style="--reveal-index:${index}">
    <b>${pinned ? '最新' : `P${escapeHtml(chapter.chapterIndex || index + 1)}`}</b><span><strong>${escapeHtml(chapter.title || `第 ${chapter.chapterIndex || index + 1} 话`)}</strong><small>JM${escapeHtml(chapter.sourceId)}${pinned && comic.updatedAtSource ? ` · ${escapeHtml(comic.updatedAtSource)}` : ''}</small></span>${icon('play')}
  </button>`;
  $('#series-book-list').classList.add('is-source-series');
  $('#series-book-list').innerHTML = `<div class="source-series-hero">
    <div class="source-series-hero-cover">${coverSource ? `<img src="${escapeHtml(coverSource)}" alt="${escapeHtml(comic.displayName)} 的封面" loading="eager" decoding="async">` : '<span class="series-book-cover-empty"></span>'}<span>${chapters.length} 话</span></div>
    <div class="source-series-hero-info"><strong>JM${escapeHtml(comic.sourceId)}</strong><span>${escapeHtml(author)} · ${chapters.length} 话</span>${tags ? `<div>${tags}</div>` : ''}</div>
  </div><div class="source-series-chapter-list">${latest ? chapterButton(latest, 0, true) : ''}<div class="online-chapter-divider">全部章节 · 正序</div>${chapters.map((chapter, index) => chapterButton(chapter, index + 1)).join('')}</div>`;
  openModal('series-dialog');
  $('#series-chapter-scroll').scrollTop = 0;
}

async function refreshSeries(seriesId) {
  const button = $('#series-refresh');
  let refreshed = false;
  setBusy(button, true, '检查中…');
  try {
    const result = await api(`/api/library-series/${seriesId}/refresh`, { method: 'POST' });
    await Promise.all([loadSeries(), loadDownloads({ notify: false }), loadCaches({ notify: false })]);
    refreshed = true;
    if (result.newCount === 0) {
      toast('检查完成，这个系列已是最新');
    } else if (result.queuedCount > 0) {
      toast(`发现 ${result.newCount} 话更新，已加入下载队列`);
    } else {
      toast(`发现 ${result.newCount} 话更新，下载任务已在队列中`);
    }
  } catch (error) {
    await loadSeries().catch(() => {});
    toast(`系列更新失败：${error.message}`, 'error', 6500);
  } finally {
    setBusy(button, false);
  }
  if (refreshed && state.activeSeries?.id === seriesId && !$('#series-dialog').classList.contains('hidden') && !$('#series-dialog').classList.contains('is-closing')) {
    showSeries(seriesId, { preserveScroll: true });
  }
}

function continueSeries(seriesId) {
  const series = state.series.find((item) => item.id === seriesId) || state.activeSeries;
  const comic = seriesResumeComic(series);
  if (!comic) return toast('这个系列暂时没有可阅读章节', 'error');
  closeModal('series-dialog');
  openReader(comic);
}

async function dissolveSeries(selectedSeries = state.activeSeries) {
  const series = selectedSeries;
  if (!series) return;
  closeModal('series-dialog');
  const isSourceSeries = series.kind === 'source' || series.isSourceSeries;
  const choice = await requestConfirmation({
    title: `${isSourceSeries ? '移除禁漫书库' : '解除系列'}「${series.displayName}」？`,
    message: '默认只解除系列关系并保留全部本子；选择删除全部时，会永久删除书架记录和本地图片文件。',
    confirmLabel: isSourceSeries ? '仅移除' : '仅解散',
    confirmTone: 'primary',
    alternativeLabel: '删除全部',
    wide: isSourceSeries
  });
  if (!choice) return;
  const deleteComics = choice === 'alternative';
  if (deleteComics) {
    const confirmed = await requestConfirmation({
      title: '再次确认删除全部本子？',
      message: `即将永久删除「${series.displayName}」中的 ${series.members.length} 本及所有本地图片文件。\n此操作无法撤销。`,
      confirmLabel: '确认删除',
      wide: isSourceSeries
    });
    if (!confirmed) return;
  }
  try {
    const suffix = deleteComics ? '?deleteComics=true' : '';
    await api(`/api/library-series/${series.id}${suffix}`, { method: 'DELETE' });
    state.activeSeries = null;
    await Promise.all([loadLibrary(), loadCollections(), loadSeries()]);
    toast(deleteComics ? '系列及全部本地文件已删除' : '系列已解散，本子保持不变');
  } catch (error) { toast(error.message, 'error'); }
}

function collectionDescendants(collectionId) {
  const found = new Set();
  const visit = (parentId) => state.collections.filter((item) => item.parentId === parentId).forEach((item) => {
    if (found.has(item.id)) return;
    found.add(item.id);
    visit(item.id);
  });
  visit(collectionId);
  return found;
}

function openCollectionDialog(collectionId = null, parentOverride = null) {
  state.editingCollectionId = collectionId;
  const editing = collectionId ? state.collections.find((item) => item.id === collectionId) : null;
  const excluded = editing ? collectionDescendants(editing.id) : new Set();
  if (editing) excluded.add(editing.id);
  const suggestedParent = editing?.parentId
    || parentOverride
    || (!editing && state.activeCollection && state.activeCollection !== 'unfiled' ? state.activeCollection : '');
  $('#collection-dialog-title').textContent = editing ? '管理收藏夹' : '新建收藏夹';
  $('#collection-name').value = editing?.name || '';
  $('#collection-parent').innerHTML = '<option value="">顶层目录</option>' + state.collections
    .filter((item) => !excluded.has(item.id))
    .map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  $('#collection-parent').value = suggestedParent || '';
  $('#delete-collection').classList.toggle('hidden', !editing);
  $('#collection-hint').classList.toggle('hidden', Boolean(editing));
  $('#save-collection span:last-child').textContent = editing ? '保存目录' : '创建目录';
  openModal('collection-dialog');
  setTimeout(() => $('#collection-name').focus(), 50);
}

async function saveCollection(event) {
  event.preventDefault();
  const name = $('#collection-name').value.trim();
  if (!name) return;
  const parentId = $('#collection-parent').value || null;
  const submit = $('#collection-form button[type="submit"]');
  const editingId = state.editingCollectionId;
  setBusy(submit, true, editingId ? '保存中…' : '创建中…');
  try {
    if (editingId) await api(`/api/collections/${editingId}`, { method: 'PATCH', body: { name, parentId } });
    else await api('/api/collections', { method: 'POST', body: { name, parentId } });
    await loadCollections();
    closeModal('collection-dialog');
    $('#view-title').textContent = currentCollectionName();
    toast(editingId ? '目录修改已保存' : (parentId ? '已创建子目录' : '已创建收藏夹'));
  } catch (error) { toast(error.message, 'error'); }
  finally { setBusy(submit, false); }
}

async function deleteCollection(collectionId = state.editingCollectionId) {
  const collection = state.collections.find((item) => item.id === collectionId);
  if (!collection) return;
  const removedIds = collectionDescendants(collection.id);
  removedIds.add(collection.id);
  closeModal('collection-dialog');
  const approved = await requestConfirmation({
    title: `删除目录「${collection.name}」？`,
    message: '目录及其子目录会被删除；其中的本子只会解除归类，不会从书架或磁盘删除。',
    confirmLabel: '删除目录'
  });
  if (!approved) return;
  try {
    await api(`/api/collections/${collection.id}`, { method: 'DELETE' });
    if (removedIds.has(state.activeCollection)) state.activeCollection = '';
    await Promise.all([loadCollections(), loadLibrary()]);
    toast('目录已删除，本子文件保持不变');
  } catch (error) { toast(error.message, 'error'); }
}

function openSettings() {
  $('#option-path').value = state.settings.optionPath || '';
  $('#proxy-address').value = state.settings.proxy || '';
  $('#download-path').value = state.settings.downloadPath || '';
  $('#sanity-mode').checked = Boolean(state.settings.sanityMode);
  renderAppUpdate();
  openModal('settings-dialog');
}

function openAccount() {
  openModal('account-dialog');
  setTimeout(() => (state.provider?.authenticated ? $('#account-password') : $('#account-username')).focus(), 40);
}

function openAppUpdate() {
  renderAppUpdate();
  openModal('app-update-dialog');
}

async function openAbout() {
  try {
    state.about ||= await api('/api/app/about');
    $('#about-version').textContent = `版本 ${state.about.version}`;
    $('#about-description').textContent = state.about.description;
  } catch (error) {
    $('#about-version').textContent = `版本 ${state.appUpdate?.currentVersion || '—'}`;
  }
  openModal('about-dialog');
}

function renderAppUpdate() {
  const update = state.appUpdate || {};
  const release = update.release || {};
  $('#app-update-version').textContent = `当前版本 ${update.currentVersion || '—'}`;
  $('#app-update-current').textContent = update.currentVersion ? `v${update.currentVersion}` : '—';
  $('#app-update-target').textContent = update.latestVersion ? `v${update.latestVersion}` : '—';
  const newBadge = $('#app-update-new');
  const hasUpdate = Boolean(update.available) && ['available', 'downloading', 'ready'].includes(update.state);
  newBadge.classList.toggle('hidden', !hasUpdate);
  newBadge.title = hasUpdate ? `JmShelf ${update.latestVersion || ''} 可用` : '发现新版本';
  let message = 'JmShelf 会在每次启动时自动检查更新。';
  if (update.state === 'checking') message = '正在连接个人应用下载站…';
  if (update.state === 'available') message = `JmShelf ${update.latestVersion} 已可用。更新只替换程序文件，不会重新运行安装包。`;
  if (update.state === 'up-to-date') message = `当前已经是最新版本 ${update.currentVersion}。`;
  if (update.state === 'downloading') message = `正在加载并校验 JmShelf ${update.latestVersion} 的更新文件…`;
  if (update.state === 'ready') message = `JmShelf ${update.latestVersion} 已校验并解压完成，可以重启应用进行替换。`;
  if (update.state === 'applying') message = 'JmShelf 即将退出、替换程序文件并自动重新启动。';
  if (update.state === 'error') message = update.error || '更新检查失败，请稍后重试。';
  $('#app-update-message').textContent = message;
  const notes = Array.isArray(release.releaseNotes) ? release.releaseNotes : [];
  $('#app-update-notes').innerHTML = notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('');
  $('#app-update-notes').classList.toggle('hidden', notes.length === 0 || update.state === 'up-to-date');
  $('#app-update-notes-empty').classList.toggle('hidden', notes.length > 0 && update.state !== 'up-to-date');
  const progress = Math.max(0, Math.min(100, Number(update.progress || 0)));
  const progressBar = $('#app-update-progress');
  progressBar.style.setProperty('--update-progress', `${progress}%`);
  progressBar.querySelector('span').textContent = `${progress}%`;
  progressBar.classList.toggle('hidden', !['downloading', 'ready'].includes(update.state));
  $('#app-update-check').disabled = ['checking', 'downloading', 'applying'].includes(update.state);
  $('#app-update-retry').disabled = ['checking', 'downloading', 'applying'].includes(update.state);
  $('#app-update-download').classList.toggle('hidden', update.state !== 'available');
  $('#app-update-apply').classList.toggle('hidden', update.state !== 'ready');
}

function showLastAppUpdateResult(update) {
  const result = update?.lastApply;
  if (!result?.operationId || !['completed', 'failed'].includes(result.state)) return;
  const storageKey = `jmshelf-update-result-${result.operationId}`;
  if (localStorage.getItem(storageKey)) return;
  localStorage.setItem(storageKey, 'shown');
  if (result.state === 'completed') {
    toast(result.message || `JmShelf ${result.version || ''} 更新完成`, 'info', 5200);
  } else {
    toast(result.message || '上次应用更新失败，旧版本已恢复', 'error', 9000);
  }
}

async function checkAppUpdate({ silent = false } = {}) {
  const previousAvailable = Boolean(state.appUpdate?.available);
  state.appUpdate = { ...state.appUpdate, state: 'checking', error: null };
  renderAppUpdate();
  try {
    state.appUpdate = await api('/api/app/update/check', { method: 'POST' });
    renderAppUpdate();
    showLastAppUpdateResult(state.appUpdate);
    if (state.appUpdate.available && !previousAvailable) {
      toast(`发现 JmShelf ${state.appUpdate.latestVersion}，点击左上角 NEW 查看`, 'info', 5200);
      if (!silent) {
        closeModal('settings-dialog');
        openAppUpdate();
      }
    } else if (!silent && state.appUpdate.available) {
      closeModal('settings-dialog');
      openAppUpdate();
    } else if (!silent && state.appUpdate.state === 'up-to-date') {
      toast('当前已经是最新版本');
    } else if (!silent && state.appUpdate.state === 'error') {
      toast(state.appUpdate.error, 'error', 6000);
    }
  } catch (error) {
    state.appUpdate = { ...state.appUpdate, state: 'error', error: error.message };
    renderAppUpdate();
    if (!silent) toast(error.message, 'error', 6000);
  }
}

async function pollAppUpdate() {
  try {
    state.appUpdate = await api('/api/app/update');
    renderAppUpdate();
    if (!['downloading'].includes(state.appUpdate.state)) {
      clearInterval(state.appUpdatePoll);
      state.appUpdatePoll = null;
      if (state.appUpdate.state === 'ready') toast('更新文件已校验完成，可以重启更新');
      if (state.appUpdate.state === 'error') toast(state.appUpdate.error, 'error', 6000);
    }
  } catch (error) {
    clearInterval(state.appUpdatePoll);
    state.appUpdatePoll = null;
    toast(error.message, 'error');
  }
}

async function downloadAppUpdate() {
  try {
    state.appUpdate = await api('/api/app/update/download', { method: 'POST' });
    renderAppUpdate();
    clearInterval(state.appUpdatePoll);
    state.appUpdatePoll = setInterval(pollAppUpdate, 700);
  } catch (error) { toast(error.message, 'error', 6000); }
}

async function applyAppUpdate() {
  const approved = await requestConfirmation({
    title: `更新到 JmShelf ${state.appUpdate.latestVersion || ''}？`,
    message: 'JmShelf 将退出，原位替换程序文件后自动重启。书库数据库、设置和下载文件不会被改动。',
    confirmLabel: '重启并更新',
    confirmTone: 'primary'
  });
  if (!approved) return;
  try {
    state.appUpdate = { ...state.appUpdate, state: 'applying' };
    renderAppUpdate();
    await api('/api/app/update/apply', { method: 'POST' });
  } catch (error) {
    state.appUpdate = { ...state.appUpdate, state: 'error', error: error.message };
    renderAppUpdate();
    toast(error.message, 'error', 6000);
  }
}

function showStartupLibraryUpdates(snapshot) {
  const updates = snapshot.updates || [];
  if (!updates.length || state.startupLibraryUpdateShown) return;
  state.startupLibraryUpdateShown = true;
  const total = updates.reduce((sum, item) => sum + Number(item.newCount || 0), 0);
  $('#library-update-summary').textContent = `${updates.length} 部禁漫书库作品共有 ${total} P 尚未下载。选择作品可查看分 P 并决定是否更新。`;
  $('#library-update-list').innerHTML = updates.map((update, index) => {
    const series = state.series.find((item) => item.id === update.seriesId);
    const cover = series?.members?.[0];
    const latest = (update.items || []).at(-1);
    return `<button type="button" class="library-update-item" data-action="open-startup-series" data-series-id="${escapeHtml(update.seriesId)}" style="--reveal-index:${index}">
      <span class="library-update-cover">
        <span class="series-book-cover-empty"></span>
        ${cover?.coverPath ? `<img src="${comicCoverUrl(cover)}" alt="${escapeHtml(update.displayName)} 的封面" loading="eager" decoding="async">` : ''}
        ${coverPrivacyMask(cover)}
        <b>${Number(update.newCount || 0)} P 更新</b>
      </span>
      <span class="library-update-copy"><strong>${escapeHtml(update.displayName)}</strong><small>${latest ? `最新 P${escapeHtml(latest.chapterIndex || '')} · ${escapeHtml(latest.title || `JM${latest.sourceId}`)}` : '点击查看更新章节'}</small></span>
    </button>`;
  }).join('');
  openModal('library-update-dialog');
}

async function waitForStartupLibraryCheck() {
  const deadline = Date.now() + 3 * 60 * 1000;
  let snapshot = null;
  while (Date.now() < deadline) {
    snapshot = await api('/api/library-series/startup-check');
    if (snapshot.state === 'complete') return snapshot;
    await new Promise((resolve) => setTimeout(resolve, 450));
  }
  return snapshot;
}

async function runStartupNetworkTasks() {
  try {
    const snapshot = await waitForStartupLibraryCheck();
    if (snapshot?.state === 'complete') {
      await loadSeries();
      showStartupLibraryUpdates(snapshot);
    }
  } catch (error) {
    console.warn('启动更新检查失败', error);
  } finally {
    checkAppUpdate({ silent: true });
    if (state.provider?.authenticated) loadJmFavorites({ render: false }).catch(() => {});
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const submit = $('#settings-form button[type="submit"]');
  setBusy(submit, true, '检测中…');
  try {
    state.settings = await api('/api/settings', { method: 'PATCH', body: {
      optionPath: $('#option-path').value.trim(),
      proxy: $('#proxy-address').value.trim(),
      downloadPath: $('#download-path').value.trim(),
      sanityMode: $('#sanity-mode').checked
    } });
    delete state.settings.ok;
    renderLibrary();
    await loadProvider();
    if (state.provider.available) {
      closeModal('settings-dialog');
      toast('信息源连接成功');
    }
  } catch (error) { toast(error.message, 'error'); }
  finally { setBusy(submit, false); }
}

function collectionPath(collection) {
  const names = [collection.name];
  const visited = new Set([collection.id]);
  let parentId = collection.parentId;
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = state.collections.find((item) => item.id === parentId);
    if (!parent) break;
    names.unshift(parent.name);
    parentId = parent.parentId;
  }
  return names.join(' / ');
}

async function selectCollection(collectionId) {
  const wasOnline = state.activeCollection === 'jm-online';
  if (!wasOnline) state.librarySearch = $('#search-input').value;
  state.activeCollection = collectionId;
  const isOnline = collectionId === 'jm-online';
  setActivityFocus(isOnline ? 'online' : 'library');
  renderQuickAction();
  $('#library-section').classList.toggle('hidden', isOnline);
  $('#online-section').classList.toggle('hidden', !isOnline);
  $('#search-input').placeholder = isOnline
    ? '在 JMonline 搜索关键词…'
    : '搜索昵称、标题、作者、标签或车牌…';
  if (isOnline) {
    $('#search-input').value = $('#online-query').value;
    renderCollections();
    if (!wasOnline) replayAnimation($('#online-section'), 'is-online-section-entering');
    loadProvider({ probe: true }).catch(() => {});
    loadOnlineRecommendations().catch(() => {});
    loadOnlineHome().catch(() => {});
    return;
  }
  if (wasOnline) $('#search-input').value = state.librarySearch;
  if (collectionId === 'jm-favorites') {
    setLibraryFilter('all');
    if (!state.provider?.authenticated) {
      state.comics = [];
      renderCollections();
      renderLibrary();
      openAccount();
      return;
    }
  }
  renderCollections();
  try {
    await loadLibrary();
  } catch (error) {
    if (collectionId === 'jm-favorites') {
      state.comics = [];
      await loadProvider().catch(() => {});
      renderLibrary();
      toast(`JM 收藏读取失败：${error.message}`, 'error', 6000);
      openAccount();
      return;
    }
    throw error;
  }
}

async function toggleComicCollection(comic, collectionId) {
  const memberships = new Set(comic.collections || []);
  const adding = !memberships.has(collectionId);
  if (adding) memberships.add(collectionId);
  else memberships.delete(collectionId);
  await api(`/api/comics/${comic.id}/collections`, {
    method: 'PUT',
    body: { collectionIds: [...memberships] }
  });
  comic.collections = [...memberships];
  await Promise.all([loadCollections(), loadLibrary(), loadSeries()]);
  renderLibrary();
  const collection = state.collections.find((item) => item.id === collectionId);
  toast(`${adding ? '已加入' : '已移出'}「${collection?.name || '目录'}」`);
}

async function toggleReadLater(comic) {
  let collection = state.collections.find((item) => item.name.trim() === '待读');
  if (!collection) {
    collection = await api('/api/collections', { method: 'POST', body: { name: '待读', parentId: null } });
    await loadCollections();
  }
  await toggleComicCollection(comic, collection.id);
}

function clearLibraryDrag() {
  state.dragPayload = null;
  document.body.classList.remove('is-library-dragging');
  $$('.is-dragging, .is-drop-target').forEach((item) => item.classList.remove('is-dragging', 'is-drop-target'));
}

function libraryDragPayload(card) {
  const comicId = card?.dataset.comicId;
  if (comicId) {
    const comic = findComic(comicId);
    if (isRemoteComic(comic)) return null;
    return comic ? { type: 'comic', id: comic.id, comicIds: [comic.id], label: comic.displayName } : null;
  }
  const seriesId = card?.dataset.seriesId;
  if (seriesId) {
    const series = state.series.find((item) => item.id === seriesId);
    return series ? { type: 'series', id: series.id, comicIds: series.memberIds, label: series.displayName } : null;
  }
  return null;
}

async function addDraggedItemsToCollection(collectionId, payload) {
  const collection = state.collections.find((item) => item.id === collectionId);
  if (!collection || !payload?.comicIds?.length) return;
  const result = await api(`/api/collections/${collectionId}/items`, {
    method: 'POST',
    body: { comicIds: payload.comicIds }
  });
  await Promise.all([loadCollections(), loadLibrary(), loadSeries()]);
  const count = payload.type === 'series' ? ` · ${payload.comicIds.length} 本` : '';
  toast(result.addedCount ? `已将「${payload.label}」加入「${collection.name}」${count}` : `「${payload.label}」已在「${collection.name}」中`);
}

function hideContextMenu() {
  const menu = $('#context-menu');
  menu.classList.add('hidden');
  menu.setAttribute('aria-hidden', 'true');
  menu.innerHTML = '';
  state.contextActions = [];
  $('#quick-add-ball').classList.remove('is-open');
}

function showContextMenu(items, x, y, { alignRight = false, alignBottom = false } = {}) {
  const menu = $('#context-menu');
  $('#quick-add-ball').classList.remove('is-open');
  state.contextActions = items.map((item) => item.action || null);
  menu.innerHTML = items.map((item, index) => {
    if (item.section) return `<div class="context-menu-section">${escapeHtml(item.section)}</div>`;
    return `<button type="button" class="context-menu-item${item.danger ? ' is-danger' : ''}" data-context-index="${index}" role="menuitem" style="--reveal-index:${index}" ${item.disabled ? 'disabled' : ''}>
      ${icon(item.icon || 'list')}<span>${escapeHtml(item.label)}</span>${item.checked ? icon('tick', 'context-menu-check') : ''}
    </button>`;
  }).join('');
  menu.classList.remove('hidden');
  menu.setAttribute('aria-hidden', 'false');
  menu.style.left = '0px';
  menu.style.top = '0px';
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    const margin = 8;
    const desiredLeft = alignRight ? x - rect.width : x;
    const desiredTop = alignBottom ? y - rect.height : y;
    menu.style.left = `${Math.max(margin, Math.min(desiredLeft, window.innerWidth - rect.width - margin))}px`;
    menu.style.top = `${Math.max(margin, Math.min(desiredTop, window.innerHeight - rect.height - margin))}px`;
  });
}

async function copyContextText(value, successMessage) {
  await navigator.clipboard.writeText(String(value || ''));
  toast(successMessage);
}

function seriesHasCollection(series, collectionId) {
  return Boolean(series?.members?.length) && series.members.every((comic) => (comic.collections || []).includes(collectionId));
}

async function toggleSeriesCollection(series, collectionId) {
  const adding = !seriesHasCollection(series, collectionId);
  await Promise.all(series.members.map((comic) => {
    const memberships = new Set(comic.collections || []);
    if (adding) memberships.add(collectionId);
    else memberships.delete(collectionId);
    return api(`/api/comics/${comic.id}/collections`, {
      method: 'PUT',
      body: { collectionIds: [...memberships] }
    });
  }));
  await Promise.all([loadCollections(), loadLibrary(), loadSeries()]);
  const collection = state.collections.find((item) => item.id === collectionId);
  toast(`系列已${adding ? '加入' : '移出'}「${collection?.name || '目录'}」`);
}

async function toggleSeriesReadLater(series) {
  let collection = state.collections.find((item) => item.name.trim() === '待读');
  if (!collection) {
    collection = await api('/api/collections', { method: 'POST', body: { name: '待读', parentId: null } });
    await loadCollections();
  }
  await toggleSeriesCollection(series, collection.id);
}

function showComicContextMenu(event, comic) {
  if (isRemoteComic(comic)) {
    showContextMenu([
      { section: comic.displayName },
      { label: '查看详情', icon: 'info', action: () => showDetail(comic.id) },
      { label: comic.inShelf ? '已在书架' : '加入书架', icon: comic.inShelf ? 'tick' : 'plus', disabled: Boolean(comic.inShelf), action: () => addFavoriteToShelf(comic) },
      { label: '下载到书架', icon: 'download', action: () => { state.activeComic = comic; return downloadFavorite(); } },
      { label: `复制车牌 JM${comic.favoriteAlbumId || comic.sourceId}`, icon: 'copy', action: async () => {
        await navigator.clipboard.writeText(`JM${comic.favoriteAlbumId || comic.sourceId}`);
        toast('车牌已复制');
      } },
    ], event.clientX, event.clientY);
    return;
  }
  const readLater = state.collections.find((item) => item.name.trim() === '待读');
  const cacheTask = activeCacheTaskForComic(comic);
  const items = [
    { section: comic.displayName },
    { label: '开始阅读', icon: 'play', action: () => openReader(comic) },
    { label: '查看详情', icon: 'info', action: () => showDetail(comic.id) },
    { label: '加入系列…', icon: 'grid-add', action: () => openSeriesBuilder(comic.id) },
    { label: readLater && comic.collections.includes(readLater.id) ? '移出待读' : '加入待读', icon: 'bookmark', checked: Boolean(readLater && comic.collections.includes(readLater.id)), action: () => toggleReadLater(comic) },
    { section: '添加到目录' },
    ...state.collections.map((collection) => ({
      label: collectionPath(collection),
      icon: 'folder',
      checked: comic.collections.includes(collection.id),
      action: () => toggleComicCollection(comic, collection.id)
    })),
    { label: '新建收藏夹…', icon: 'plus', action: () => openCollectionDialog() },
  ];
  if (comic.sourceId && (!comic.rootPath || comic.storageKind === 'cache' || cacheTask)) {
    items.push({ section: '缓存管理' });
    if (cacheTask) {
      items.push({ label: '正在缓存…', icon: 'download', disabled: true });
    } else if (comic.storageKind === 'cache' && comic.rootPath) {
      items.push({ label: '清除阅读缓存', icon: 'trash', action: () => clearComicCaches([comic]) });
    } else {
      items.push({ label: '缓存以供阅读', icon: 'download', action: () => cacheComics([comic]) });
    }
  }
  items.push(
    { section: '其他操作' },
    { label: '在资源管理器中显示', icon: 'folder', disabled: !comic.rootPath, action: () => api(`/api/comics/${comic.id}/reveal`, { method: 'POST' }) },
    { label: '更新 JM 信息', icon: 'refresh', disabled: !comic.sourceId, action: () => { state.activeComic = comic; return rematchComic(); } },
    { label: comic.sourceId ? `复制车牌 JM${comic.sourceId}` : '复制本地路径', icon: 'copy', disabled: !comic.sourceId && !comic.rootPath, action: () => copyContextText(comic.sourceId ? `JM${comic.sourceId}` : comic.rootPath, comic.sourceId ? '车牌已复制' : '本地路径已复制') },
    ...(comic.sourceId && comic.rootPath ? [{ label: '复制本地路径', icon: 'copy', action: () => copyContextText(comic.rootPath, '本地路径已复制') }] : []),
    { label: '移出书架…', icon: 'trash', danger: true, action: () => removeComic(comic) }
  );
  showContextMenu(items, event.clientX, event.clientY);
}

function showSeriesContextMenu(event, series) {
  const resume = seriesResumeComic(series);
  const isSourceSeries = series.kind === 'source' || series.isSourceSeries;
  const items = [
    { section: series.displayName },
    { label: isSourceSeries ? '打开禁漫书库' : '打开系列', icon: 'book', action: () => showSeries(series.id) },
    { label: resume?.lastReadAt ? '继续上次阅读' : '从第一章开始', icon: 'play', disabled: !resume, action: () => continueSeries(series.id) },
    { label: '查看主本详情', icon: 'info', disabled: !series.members[0], action: () => showDetail(series.members[0].id) },
  ];
  const readLater = state.collections.find((item) => item.name.trim() === '待读');
  items.push({
    label: readLater && seriesHasCollection(series, readLater.id) ? '移出待读' : '加入待读',
    icon: 'bookmark',
    checked: Boolean(readLater && seriesHasCollection(series, readLater.id)),
    action: () => toggleSeriesReadLater(series),
  });
  items.push({ section: '添加到目录' });
  for (const collection of state.collections) {
    items.push({
      label: collectionPath(collection),
      icon: 'folder',
      checked: seriesHasCollection(series, collection.id),
      action: () => toggleSeriesCollection(series, collection.id),
    });
  }
  items.push({ label: '新建收藏夹…', icon: 'plus', action: () => openCollectionDialog() });
  const cachedMembers = series.members.filter((comic) => comic.storageKind === 'cache');
  const uncachedMembers = series.members.filter((comic) => comic.sourceId && !comic.rootPath);
  const caching = series.members.some((comic) => activeCacheTaskForComic(comic));
  if (cachedMembers.length || uncachedMembers.length || caching) {
    items.push({ section: '缓存管理' });
    if (caching) {
      items.push({ label: '正在缓存…', icon: 'download', disabled: true });
    } else if (uncachedMembers.length) {
      items.push({ label: '缓存未下载章节', icon: 'download', action: () => cacheComics(uncachedMembers) });
    }
    if (!caching && cachedMembers.length) {
      items.push({ label: '清除系列缓存', icon: 'trash', action: () => clearComicCaches(cachedMembers) });
    }
  }
  items.push({ section: '其他操作' });
  if (series.updateSupported) {
    items.push({ label: '检查更新', icon: 'refresh', action: () => refreshSeries(series.id) });
  }
  if (isSourceSeries && series.sourceSeriesId) {
    items.push({ label: `复制系列车牌 JM${series.sourceSeriesId}`, icon: 'copy', action: () => copyContextText(`JM${series.sourceSeriesId}`, '系列车牌已复制') });
  }
  items.push({ label: isSourceSeries ? '移除禁漫书库' : '解除系列', icon: 'trash', danger: true, action: () => dissolveSeries(series) });
  showContextMenu(items, event.clientX, event.clientY);
}

function showCollectionContextMenu(event, collection) {
  const siblingParent = collection.parentId || null;
  showContextMenu([
    { section: collectionPath(collection) },
    { label: '打开目录', icon: 'folder', action: () => selectCollection(collection.id) },
    { label: '新建子目录', icon: 'plus', action: () => openCollectionDialog(null, collection.id) },
    { label: '新建同级目录', icon: 'grid-add', action: () => openCollectionDialog(null, siblingParent) },
    { label: '重命名或移动', icon: 'edit', action: () => openCollectionDialog(collection.id) },
    { label: '删除目录', icon: 'trash', danger: true, action: () => deleteCollection(collection.id) }
  ], event.clientX, event.clientY);
}

function showSmartCollectionContextMenu(event, collectionId) {
  const smartName = collectionId === 'jm-favorites' ? 'JM 收藏' : (collectionId === 'unfiled' ? '未分类' : '全部本子');
  const items = [{ section: smartName }];
  if (collectionId === 'jm-favorites') {
    items.push({ label: '刷新 JM 收藏', icon: 'refresh', action: () => loadJmFavorites({ refresh: true, render: true }) });
    items.push({ label: '登录与账号', icon: 'user', action: openAccount });
  } else {
    items.push({ label: '打开此分类', icon: collectionId === 'unfiled' ? 'document' : 'grid', action: () => selectCollection(collectionId) });
    items.push({ label: '导入文件夹', icon: 'import', action: importFolder });
    items.push({ label: '查车牌', icon: 'search', action: openQuery });
    items.push({ label: '添加系列', icon: 'grid-add', action: () => openSeriesBuilder() });
    items.push({ label: '新建收藏夹', icon: 'plus', action: () => openCollectionDialog() });
  }
  showContextMenu(items, event.clientX, event.clientY);
}

function showQuickAddMenu() {
  const rect = $('#quick-add-ball').getBoundingClientRect();
  showContextMenu([
    { section: '快捷添加' },
    { label: '添加系列', icon: 'grid-add', action: () => openSeriesBuilder() },
    { label: '查车牌', icon: 'search', action: openQuery },
    { label: '导入文件夹', icon: 'import', action: importFolder }
  ], rect.right, rect.top - 8, { alignRight: true, alignBottom: true });
  $('#quick-add-ball').classList.add('is-open');
}

function renderQuickAction() {
  const button = $('#quick-add-ball');
  if (!button) return;
  const anchor = $('#download-queue-anchor');
  const isOnline = state.activeCollection === 'jm-online';
  const searchAction = isOnline && state.online.view === 'home';
  const homeAction = isOnline && state.online.view !== 'home';
  button.classList.toggle('is-online-home-action', searchAction);
  button.classList.toggle('is-online-search-action', homeAction);
  anchor.classList.toggle('is-online-navigation', isOnline);
  if (searchAction) {
    button.innerHTML = icon('search');
    button.title = '搜索；悬停打开搜索与分类';
    button.setAttribute('aria-label', '打开 JMonline 搜索');
  } else if (homeAction) {
    button.innerHTML = icon('home');
    button.title = '返回 JMonline 首页';
    button.setAttribute('aria-label', '返回 JMonline 首页');
  } else {
    button.innerHTML = icon('plus');
    button.title = '添加；悬停查看下载队列';
    button.setAttribute('aria-label', '打开添加菜单');
  }
}

function showOnlineNavigationMenu() {
  const rect = $('#quick-add-ball').getBoundingClientRect();
  showContextMenu([
    { section: 'JMonline' },
    { label: '搜索', icon: 'search', action: () => setOnlineView('search', { focus: true }) },
    { label: '分类', icon: 'grid', action: openOnlineCategories },
  ], rect.right, rect.top - 8, { alignRight: true, alignBottom: true });
  $('#quick-add-ball').classList.add('is-open');
}

$('#context-menu').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-context-index]');
  if (!button || button.disabled) return;
  event.stopPropagation();
  const action = state.contextActions[Number(button.dataset.contextIndex)];
  hideContextMenu();
  try { await action?.(); }
  catch (error) { toast(error.message, 'error', 5200); }
});

document.addEventListener('click', async (event) => {
  if (!event.target.closest('#quick-add-ball')) hideContextMenu();
  if (!event.target.closest('#reader-auto-controls') && state.readerAutoSettingsOpen) setReaderAutoSettings(false);
  const closeButton = event.target.closest('[data-close-modal]');
  if (closeButton) {
    closeModal(closeButton.dataset.closeModal);
    return;
  }
  if (event.target.classList.contains('modal')) {
    closeModal(event.target.id);
    return;
  }
  const collection = event.target.closest('[data-collection]');
  if (collection) {
    await selectCollection(collection.dataset.collection);
    return;
  }
  const actionTarget = event.target.closest('[data-action]');
  const action = actionTarget?.dataset.action;
  if (action === 'online-search-tag') {
    await searchOnlineMetadata(actionTarget.dataset.onlineQuery, 'tag');
    return;
  }
  if (action === 'online-search-metadata') {
    await searchOnlineMetadata(actionTarget.dataset.onlineQuery, actionTarget.dataset.onlineMode || 'tag');
    return;
  }
  if (action === 'online-detail') {
    showDetail(`jm-online:${actionTarget.dataset.onlineId}`);
    return;
  }
  if (action === 'online-add') {
    const comic = onlineComicById(actionTarget.dataset.onlineId);
    if (comic) await addFavoriteToShelf(comic);
    return;
  }
  if (action === 'online-download') {
    const comic = onlineComicById(actionTarget.dataset.onlineId);
    if (comic) {
      state.activeComic = comic;
      await downloadFavorite();
      state.activeComic = null;
    }
    return;
  }
  if (action === 'continue-series') {
    continueSeries(actionTarget.dataset.seriesId);
    return;
  }
  if (action === 'refresh-series') {
    refreshSeries(actionTarget.dataset.seriesId);
    return;
  }
  const card = event.target.closest('.comic-card');
  if (card) return Date.now() < state.suppressCardClickUntil ? undefined : showDetail(card.dataset.comicId);
  const seriesCard = event.target.closest('.series-card');
  if (seriesCard) return Date.now() < state.suppressCardClickUntil ? undefined : showSeries(seriesCard.dataset.seriesId);
  const readerPage = event.target.closest('.reader-page');
  if (readerPage && state.readerMode === 'book') {
    const leftPage = readerPage.classList.contains('spread-left');
    turnReaderPage(state.readerBookLeftForward === leftPage ? 1 : -1);
    return;
  }
  if (action === 'close-detail') closeDetail();
  if (action === 'empty-import') importFolder();
  if (action === 'read') openReader();
  if (action === 'preview-online') previewOnlineComic();
  if (action === 'select-online-chapter') showOnlineSeries();
  if (action === 'preview-online-latest') {
    const comic = state.activeComic;
    const latest = onlineSeriesChapters(comic).at(-1);
    if (comic && latest) previewOnlineComic(comic, latest);
  }
  if (action === 'preview-online-chapter') {
    const parent = onlineItems().find((item) => String(item.sourceId) === String(actionTarget.dataset.onlineAlbumId));
    const chapter = parent?.episodeManifest?.find((item) => String(item.sourceId) === String(actionTarget.dataset.onlineChapterId));
    closeModal('series-dialog');
    if (parent && chapter) previewOnlineComic(parent, chapter);
  }
  if (action === 'retry-reader-pending' && state.readerPendingComic) {
    if (state.readerPreview) previewOnlineComic(state.readerPendingComic);
    else openReader(state.readerPendingComic);
  }
  if (action === 'rematch') rematchComic();
  if (action === 'remove-comic') removeComic();
  if (action === 'choose-comic-cover') chooseComicCover();
  if (action === 'add-query-result') addQueryResult();
  if (action === 'add-query-item') addQueryResult(Number(event.target.closest('[data-query-index]').dataset.queryIndex));
  if (action === 'download-query-result') downloadQueryResult();
  if (action === 'download-favorite') downloadFavorite();
  if (action === 'add-favorite-to-shelf') addFavoriteToShelf();
  if (action === 'open-startup-series') {
    closeModal('library-update-dialog');
    showSeries(actionTarget.dataset.seriesId);
  }
  if (action === 'retry-download') retryDownload(event.target.closest('[data-download-task]').dataset.downloadTask).catch((error) => toast(error.message, 'error'));
  if (action === 'panic-hide') panicHideApp();
  if (action === 'close-reader') closeReader();
  if (action === 'toggle-reader-directory') setReaderDirectory(!$('#reader-directory').classList.contains('is-open'));
  if (action === 'reader-chapter-prev') switchReaderChapter(-1);
  if (action === 'reader-chapter-next') switchReaderChapter(1);
  if (action === 'select-reader-chapter') switchReaderChapter(event.target.closest('[data-comic-id]').dataset.comicId);
  if (action === 'reader-top') readerToFirstPage();
  if (action === 'reader-prev') turnReaderPage(-1);
  if (action === 'reader-next') turnReaderPage(1);
  if (action === 'toggle-reader-auto') toggleReaderAutoScroll();
  if (action === 'toggle-reader-auto-settings') setReaderAutoSettings(!state.readerAutoSettingsOpen);
  if (action === 'toggle-book-direction') toggleReaderBookDirection();
  if (action === 'select-series-comic') selectSeriesComic(event.target.closest('[data-comic-id]').dataset.comicId);
  if (action === 'remove-series-selection') removeSeriesSelection(event.target.closest('[data-comic-id]').dataset.comicId);
  if (action === 'move-series-selection') {
    const target = event.target.closest('[data-comic-id]');
    moveSeriesSelection(target.dataset.comicId, target.dataset.direction);
  }
  if (action === 'show-series-comic-detail') {
    const comicId = event.target.closest('[data-comic-id]').dataset.comicId;
    const comic = state.activeSeries?.members.find((item) => item.id === comicId);
    closeModal('series-dialog');
    if (comic) showDetail(comic.id);
  }
  if (action === 'read-source-series-comic') {
    const comicId = event.target.closest('[data-comic-id]').dataset.comicId;
    const comic = state.activeSeries?.members.find((item) => item.id === comicId);
    closeModal('series-dialog');
    if (comic) openReader(comic);
  }
  if (action === 'dissolve-series') dissolveSeries();
  if (action === 'manage-collection') openCollectionDialog(event.target.closest('[data-collection-id]').dataset.collectionId);
  if (action === 'confirm-cancel') closeModal('confirm-dialog', false);
  if (action === 'confirm-accept') closeModal('confirm-dialog', true);
  if (action === 'confirm-alternative') closeModal('confirm-dialog', 'alternative');
});

$('#comic-grid').addEventListener('dragstart', (event) => {
  const card = event.target.closest('.comic-card, .series-card');
  const payload = libraryDragPayload(card);
  if (!card || !payload || !event.dataTransfer) return;
  state.dragPayload = payload;
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('application/x-jmshelf-library-item', JSON.stringify(payload));
  event.dataTransfer.setData('text/plain', payload.label);
  document.body.classList.add('is-library-dragging');
  requestAnimationFrame(() => card.classList.add('is-dragging'));
});

$('#comic-grid').addEventListener('dragend', () => {
  state.suppressCardClickUntil = Date.now() + 180;
  clearLibraryDrag();
});

$('#collection-tree').addEventListener('dragover', (event) => {
  const row = event.target.closest('[data-collection-row]');
  if (!row || !state.dragPayload) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  $$('.collection-row.is-drop-target').forEach((item) => item !== row && item.classList.remove('is-drop-target'));
  row.classList.add('is-drop-target');
});

$('#collection-tree').addEventListener('dragleave', (event) => {
  const row = event.target.closest('[data-collection-row]');
  if (row && !row.contains(event.relatedTarget)) row.classList.remove('is-drop-target');
});

$('#collection-tree').addEventListener('drop', async (event) => {
  const row = event.target.closest('[data-collection-row]');
  if (!row) return;
  event.preventDefault();
  event.stopPropagation();
  const payload = state.dragPayload;
  clearLibraryDrag();
  try { await addDraggedItemsToCollection(row.dataset.collectionRow, payload); }
  catch (error) { toast(error.message, 'error', 5200); }
});

$('#detail-scrim').addEventListener('click', closeDetail);
$('#query-form').addEventListener('submit', queryPlate);
$('#detail-form').addEventListener('submit', saveDetail);
$('#detail-cover-privacy-enabled').addEventListener('input', updateDetailPrivacyPreview);
$('#detail-cover-privacy-direction').addEventListener('change', updateDetailPrivacyPreview);
$('#detail-cover-privacy-start').addEventListener('input', updateDetailPrivacyPreview);
$('#add-collection').addEventListener('click', () => openCollectionDialog());
$('#collection-form').addEventListener('submit', saveCollection);
$('#series-builder-form').addEventListener('submit', createSeries);
$('#delete-collection').addEventListener('click', () => deleteCollection());
$('#open-account').addEventListener('click', openAccount);
$('#open-settings').addEventListener('click', openSettings);
$('#theme-toggle').addEventListener('click', toggleTheme);
$('#settings-form').addEventListener('submit', saveSettings);
$('#app-update-check').addEventListener('click', () => checkAppUpdate());
$('#app-update-retry').addEventListener('click', () => checkAppUpdate());
$('#app-update-new').addEventListener('click', openAppUpdate);
$('#app-update-download').addEventListener('click', downloadAppUpdate);
$('#app-update-apply').addEventListener('click', applyAppUpdate);
$('#open-about').addEventListener('click', openAbout);
$('#account-login').addEventListener('click', loginProviderAccount);
$('#account-logout').addEventListener('click', logoutProviderAccount);
$('#select-download-path').addEventListener('click', async () => {
  try {
    const selected = await api('/api/dialog/folder', { method: 'POST' });
    if (selected.path) $('#download-path').value = selected.path;
  } catch (error) { toast(error.message, 'error'); }
});
$('#quick-add-ball').addEventListener('click', (event) => {
  event.stopPropagation();
  if (state.activeCollection === 'jm-online') {
    hideContextMenu();
    if (state.online.view === 'home') setOnlineView('search', { focus: true });
    else setOnlineView('home');
    return;
  }
  if ($('#context-menu').classList.contains('hidden')) showQuickAddMenu(); else hideContextMenu();
});
$('#quick-add-ball').addEventListener('mouseenter', () => {
  if (state.activeCollection === 'jm-online' && state.online.view === 'home') showOnlineNavigationMenu();
});
$('#quick-add-ball').addEventListener('contextmenu', (event) => {
  if (state.activeCollection !== 'jm-online') return;
  event.preventDefault();
  event.stopPropagation();
  showOnlineNavigationMenu();
});
$('#panic-ball').addEventListener('click', panicHideApp);
$('#window-minimize').addEventListener('click', () => nativeWindowAction('minimize'));
$('#window-maximize').addEventListener('click', () => nativeWindowAction('toggle-maximize'));
$('#window-close').addEventListener('click', () => nativeWindowAction('close'));
$('#reader-width').addEventListener('input', (event) => $('#reader-pages').style.setProperty('--reader-width', `${event.target.value}px`));
$$('.reader-mode-button').forEach((button) => button.addEventListener('click', () => setReaderMode(button.dataset.readerMode)));
$('#reader-auto-mode').addEventListener('change', (event) => setReaderAutoMode(event.target.value));
$('#reader-auto-pace').addEventListener('change', (event) => setReaderAutoPace(event.target.value));
$('#reader-pages').addEventListener('pointerdown', () => stopReaderAutoScroll(true));
$('#reader').addEventListener('wheel', () => stopReaderAutoScroll(true), { passive: true });
$('#density-select').addEventListener('change', (event) => {
  localStorage.setItem('jmshelf-density', event.target.value);
  renderLibrary();
});
$('#library-sort').addEventListener('change', (event) => {
  state.librarySort = event.target.value;
  localStorage.setItem('jmshelf-library-sort', state.librarySort);
  renderLibrary();
});
$('#library-filter').addEventListener('click', (event) => {
  const button = event.target.closest('[data-library-filter]');
  if (button) setLibraryFilter(button.dataset.libraryFilter);
});
$('#search-input').addEventListener('input', () => {
  clearTimeout(state.searchTimer);
  if (state.activeCollection === 'jm-online') {
    $('#online-query').value = $('#search-input').value;
    setOnlineView('search');
    state.searchTimer = setTimeout(() => {
      if ($('#online-query').value.trim()) searchOnline(1);
    }, 520);
    return;
  }
  state.librarySearch = $('#search-input').value;
  state.searchTimer = setTimeout(() => loadLibrary().catch((error) => toast(error.message, 'error')), 180);
});
$('#online-search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  clearTimeout(state.searchTimer);
  $('#search-input').value = $('#online-query').value;
  searchOnline(1);
});
$('#online-query').addEventListener('input', () => {
  if (state.activeCollection === 'jm-online') $('#search-input').value = $('#online-query').value;
});
$('#online-time').addEventListener('change', () => {
  $('#online-custom-dates').classList.toggle('hidden', $('#online-time').value !== 'custom');
  if (state.online.hasSearched && $('#online-time').value !== 'custom') rerunOnlineResults();
});
['online-mode', 'online-sort', 'online-category'].forEach((id) => {
  $(`#${id}`).addEventListener('change', () => {
    if (id === 'online-category' && state.online.browseCategory) state.online.browseCategory = $(`#${id}`).value;
    renderOnlineCategorySelection();
    if (state.online.hasSearched) rerunOnlineResults();
  });
});
['online-category-sort', 'online-category-time'].forEach((id) => {
  $(`#${id}`).addEventListener('change', () => {
    if (state.online.browseCategory) browseOnlineCategory(state.online.browseCategory, 1);
  });
});
['online-date-from', 'online-date-to'].forEach((id) => {
  $(`#${id}`).addEventListener('change', () => {
    if (state.online.hasSearched && $('#online-time').value === 'custom') rerunOnlineResults();
  });
});
$$('[data-online-token]').forEach((button) => button.addEventListener('click', () => {
  const input = $('#online-query');
  const separator = input.value.trim() ? ' ' : '';
  input.value = `${input.value.trim()}${separator}${button.dataset.onlineToken}`;
  $('#search-input').value = input.value;
  input.focus();
}));
$('#online-prev').addEventListener('click', () => loadOnlinePage(Math.max(1, Number(state.online.response?.page || 1) - 1)));
$('#online-next').addEventListener('click', () => loadOnlinePage(Number(state.online.response?.page || 1) + 1));
$('#online-refresh-recommendations').addEventListener('click', () => loadOnlineRecommendations({ refresh: true }));
$('#online-exclude-tags-toggle').addEventListener('click', () => {
  const panel = $('#online-exclude-tags-panel');
  const open = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !open);
  $('#online-exclude-tags-toggle').setAttribute('aria-expanded', String(open));
  renderRecommendationTagFilter();
  if (open) requestAnimationFrame(() => $('#online-exclude-tag-query').focus());
});
$('#online-exclude-tag-query').addEventListener('input', renderRecommendationTagFilter);
$('#online-exclude-tag-query').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  const first = $('#online-exclude-tag-results [data-first-excluded-result]');
  addExcludedRecommendationTag(first?.dataset.addExcludedTag || event.currentTarget.value);
});
$('#online-exclude-tags-panel').addEventListener('click', (event) => {
  const add = event.target.closest('[data-add-excluded-tag]');
  if (add) {
    addExcludedRecommendationTag(add.dataset.addExcludedTag);
    return;
  }
  const remove = event.target.closest('[data-remove-excluded-tag]');
  if (remove) removeExcludedRecommendationTag(remove.dataset.removeExcludedTag);
});
$('#online-clear-excluded-tags').addEventListener('click', () => setExcludedRecommendationTags([]));
$('#online-clear-temp-cache').addEventListener('click', clearOnlineTempCache);
$('#online-recommendation-sort').addEventListener('change', (event) => {
  state.online.recommendationSort = event.target.value;
  state.online.recommendationBatch = 0;
  try { localStorage.setItem(ONLINE_RECOMMEND_SORT_KEY, state.online.recommendationSort); } catch (_) {}
  loadOnlineRecommendations({ poll: true }).catch((error) => toast(error.message, 'error'));
});
$('#online-profile-tags').addEventListener('click', (event) => {
  const button = event.target.closest('[data-profile-tag]');
  if (!button) return;
  startOnlineSearch(button.dataset.profileTag, button.dataset.profileMode || 'tag');
});
$('#online-section').addEventListener('click', (event) => {
  const category = event.target.closest('[data-online-category-browse]');
  if (!category) return;
  browseOnlineCategory(category.dataset.onlineCategoryBrowse, 1);
});
$('#online-search-form').addEventListener('click', (event) => {
  const suggestion = event.target.closest('[data-online-suggestion]');
  if (suggestion) {
    startOnlineSearch(suggestion.dataset.onlineSuggestion, suggestion.dataset.onlineSuggestionMode || 'site');
    return;
  }
  const recent = event.target.closest('[data-online-recent]');
  if (recent) {
    startOnlineSearch(recent.dataset.onlineRecent, recent.dataset.onlineRecentMode || 'site');
    return;
  }
  if (event.target.closest('#online-clear-history')) {
    state.online.recentSearches = [];
    try { localStorage.removeItem(ONLINE_SEARCH_HISTORY_KEY); } catch (_) {}
    renderOnlineSearchAssists();
    return;
  }
});
$('#exit-app').addEventListener('click', async () => {
  if (window.pywebview?.api?.window_action) await nativeWindowAction('close');
  else {
    await api('/api/app/exit', { method: 'POST' }).catch(() => {});
    window.close();
  }
});

document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    $('#search-input').focus();
  }
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'h') {
    event.preventDefault();
    panicHideApp();
    return;
  }
  if (event.key === 'Escape') {
    if (!$('#context-menu').classList.contains('hidden')) hideContextMenu();
    else if (state.readerAutoSettingsOpen) setReaderAutoSettings(false);
    else {
      const openModals = $$('.modal:not(.hidden)');
      if (openModals.length) closeModal(openModals.at(-1).id);
      else if (!$('#reader').classList.contains('hidden')) closeReader();
      else if ($('#detail-drawer').classList.contains('open')) closeDetail();
    }
  }
  const readerOpen = !$('#reader').classList.contains('hidden');
  const typing = event.target.matches('input, textarea, select');
  if (readerOpen && !typing && state.readerMode !== 'scroll' && ['ArrowLeft', 'PageUp', 'ArrowRight', 'PageDown'].includes(event.key)) {
    event.preventDefault();
    let direction = ['ArrowLeft', 'PageUp'].includes(event.key) ? -1 : 1;
    if (state.readerMode === 'book' && state.readerBookLeftForward && ['ArrowLeft', 'ArrowRight'].includes(event.key)) direction *= -1;
    turnReaderPage(direction);
  }
  if (readerOpen && state.readerAutoRunning && ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp', ' '].includes(event.key)) stopReaderAutoScroll(true);
  if (readerOpen && !typing && event.key === '[') switchReaderChapter(-1);
  if (readerOpen && !typing && event.key === ']') switchReaderChapter(1);
});

document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopReaderAutoScroll();
});

document.addEventListener('mousedown', (event) => {
  if (event.button !== 1) return;
  event.preventDefault();
}, true);

document.addEventListener('auxclick', (event) => {
  if (event.button !== 1) return;
  event.preventDefault();
  panicHideApp();
});

document.addEventListener('contextmenu', (event) => {
  const onlineCard = event.target.closest('.online-card');
  if (onlineCard) {
    const comic = onlineComicById(onlineCard.dataset.onlineId);
    if (!comic) return;
    event.preventDefault();
    showComicContextMenu(event, comic);
    return;
  }
  const comicCard = event.target.closest('.comic-card');
  if (comicCard) {
    const comic = findComic(comicCard.dataset.comicId);
    if (!comic) return;
    event.preventDefault();
    showComicContextMenu(event, comic);
    return;
  }
  const seriesCard = event.target.closest('.series-card');
  if (seriesCard) {
    const series = state.series.find((item) => item.id === seriesCard.dataset.seriesId);
    if (!series) return;
    event.preventDefault();
    showSeriesContextMenu(event, series);
    return;
  }
  const seriesMember = event.target.closest('.series-book-entry, .source-series-chapter');
  if (seriesMember) {
    const comic = state.activeSeries?.members?.find((item) => item.id === seriesMember.dataset.comicId);
    if (!comic) return;
    event.preventDefault();
    showComicContextMenu(event, comic);
    return;
  }
  const collectionRow = event.target.closest('[data-collection-row]');
  if (collectionRow) {
    const collection = state.collections.find((item) => item.id === collectionRow.dataset.collectionRow);
    if (!collection) return;
    event.preventDefault();
    showCollectionContextMenu(event, collection);
    return;
  }
  const smartCollection = event.target.closest('.nav-item[data-collection]');
  if (smartCollection) {
    event.preventDefault();
    showSmartCollectionContextMenu(event, smartCollection.dataset.collection || '');
  }
});

async function init() {
  applyTheme(document.documentElement.dataset.theme, { persist: false });
  try {
    const savedOnlineSearches = JSON.parse(localStorage.getItem(ONLINE_SEARCH_HISTORY_KEY) || '[]');
    state.online.recentSearches = Array.isArray(savedOnlineSearches)
      ? savedOnlineSearches.filter((item) => item && typeof item.query === 'string').slice(0, 8).map((item) => ({ query: item.query, mode: item.mode || 'site' }))
      : [];
  } catch (_) {
    state.online.recentSearches = [];
  }
  try {
    const savedExcludedTags = JSON.parse(localStorage.getItem(ONLINE_RECOMMEND_EXCLUDED_TAGS_KEY) || '[]');
    state.online.excludedRecommendationTags = Array.isArray(savedExcludedTags)
      ? uniqueRecommendationTags(savedExcludedTags)
      : [];
  } catch (_) {
    state.online.excludedRecommendationTags = [];
  }
  $('#density-select').value = localStorage.getItem('jmshelf-density') || 'comfortable';
  const savedRecommendationSort = localStorage.getItem(ONLINE_RECOMMEND_SORT_KEY) || 'diverse';
  state.online.recommendationSort = [...$('#online-recommendation-sort').options].some((option) => option.value === savedRecommendationSort)
    ? savedRecommendationSort
    : 'diverse';
  $('#online-recommendation-sort').value = state.online.recommendationSort;
  renderRecommendationTagFilter();
  state.libraryFilter = localStorage.getItem('jmshelf-library-filter') || 'all';
  const savedSort = localStorage.getItem('jmshelf-library-sort') || 'updated';
  state.librarySort = [...$('#library-sort').options].some((option) => option.value === savedSort) ? savedSort : 'updated';
  $('#library-sort').value = state.librarySort;
  setOnlineView(state.online.view);
  setActivityFocus('library');
  renderOnlineSearchAssists();
  try {
    const [settings, onlinePreferences] = await Promise.all([
      api('/api/settings'), api('/api/online/preferences'), loadCollections(), loadLibrary(), loadSeries(), loadProvider(), loadDownloads({ notify: false }), loadCaches({ notify: false })
    ]);
    state.settings = settings;
    if (onlinePreferences.persisted) {
      state.online.excludedRecommendationTags = uniqueRecommendationTags(onlinePreferences.excludedRecommendationTags || []);
    } else if (state.online.excludedRecommendationTags.length) {
      persistExcludedRecommendationTags().catch(() => {});
    }
    renderRecommendationTagFilter();
    renderLibrary();
  } catch (error) {
    toast(`初始化失败：${error.message}`, 'error', 8000);
  }
  setInterval(() => api('/api/heartbeat', { method: 'POST' }).catch(() => {}), 15_000);
  setInterval(() => loadDownloads().catch(() => {}), 1100);
  setInterval(() => loadCaches().catch(() => {}), 1100);
  setTimeout(() => runStartupNetworkTasks(), 450);
  setInterval(() => loadSeries({ onlyIfChanged: true }).catch(() => {}), 60_000);
  api('/api/heartbeat', { method: 'POST' }).catch(() => {});
}

init();
