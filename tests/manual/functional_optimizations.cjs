const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../public/app.js'), 'utf8');
function extract(first, next) { return source.slice(source.indexOf(first), source.indexOf(next, source.indexOf(first))); }

(async () => {
  let polls = [];
  const home = {
    state: { activeCollection: 'jm-online', online: { homeLoaded: false, homeLoading: false, homeCheckedAt: 0 } },
    renderOnlineHome() {}, remoteOnlineComic: (value) => value, clearTimeout() {},
    setTimeout(callback) { polls.push(callback); return polls.length; },
    api: async () => ({ refreshing: true, updatedAt: 'old', sections: [{ key: 'latest', items: [] }] }),
  };
  vm.createContext(home);
  vm.runInContext(extract('async function loadOnlineHome(', 'async function browseOnlineCategory('), home);
  await home.loadOnlineHome();
  assert.equal(polls.length, 1, 'An existing stale ranking must still receive the background refresh');
  home.api = async () => ({ refreshing: false, updatedAt: 'new', sections: [{ key: 'latest', items: [{ albumId: '701' }] }] });
  await polls.shift()();
  assert.equal(home.state.online.homeUpdatedAt, 'new');
  assert.equal(home.state.online.homeSections[0].items[0].albumId, '701');

  const cancelled = [];
  const query = {
    onlineRequestPrefix: 'test', AbortController,
    state: { online: { metadataControllers: new Map(), metadataQueue: [], metadataQueued: new Set(), metadataLoading: new Set(), metadataRevision: 0 } },
    api: async (_url, options) => { cancelled.push(options.body.requestToken); },
  };
  vm.createContext(query);
  vm.runInContext(extract('function cancelOnlineQueries()', 'function setActivityFocus('), query);
  const first = query.beginOnlineQuery(1);
  const metadata = new AbortController();
  query.state.online.metadataControllers.set('old-metadata', metadata);
  const second = query.beginOnlineQuery(2);
  assert(first.signal.aborted && metadata.signal.aborted);
  assert(!second.signal.aborted);
  assert.deepEqual(cancelled, ['test-query-1', 'old-metadata']);

  polls = [];
  const shown = [];
  const preview = {
    state: { readerPreview: true, readerPreviewAlbumId: '701', readerPages: [] },
    $: () => ({ classList: { contains: () => false } }),
    clearTimeout() {}, setTimeout(callback) { polls.push(callback); return polls.length; },
    api: async () => ({ status: 'loading', pageCount: 7, pages: Array.from({ length: 6 }, (_, index) => ({ index })) }),
    appendReaderPages(_comic, pages) { shown.push(pages.length); preview.state.readerPages = pages; },
    updatePreviewStreamState() {}, toast() { throw new Error('Unexpected preview failure'); },
  };
  vm.createContext(preview);
  vm.runInContext(extract('function startPreviewStreamUpdates(', 'function startReaderStreamUpdates('), preview);
  preview.startPreviewStreamUpdates({ pageCount: 7 }, '701');
  await polls.shift()();
  assert.equal(shown[0], 4, 'Only complete four-page groups should be displayed');
  preview.api = async () => ({ status: 'ready', pageCount: 7, pages: Array.from({ length: 7 }, (_, index) => ({ index })) });
  await polls.shift()();
  assert.equal(shown[1], 7, 'Final partial group must be shown');

  const favoriteNodes = new Map();
  let failSecond = false;
  const favorites = {
    URLSearchParams,
    state: { provider: { authenticated: true }, jmFavoritesRequestId: 0, activeCollection: 'jm-favorites' },
    $: (key) => { if (!favoriteNodes.has(key)) favoriteNodes.set(key, { value: '', textContent: '' }); return favoriteNodes.get(key); },
    setBusy() {}, renderLibrary() {}, remoteFavoriteComic: (item) => ({ id: item.albumId }),
    api: async (url) => {
      if (url === '/api/provider/favorites/state') return [];
      const page = Number(new URL(url, 'http://localhost').searchParams.get('page'));
      if (page === 2 && failSecond) throw new Error('Temporary page failure');
      const item = { albumId: String(700 + page), title: 'Favorite' };
      return { username: 'user', page, pageCount: 2, hasMore: page < 2, total: 2, items: [item], remoteItems: [item], localComics: [] };
    },
  };
  vm.createContext(favorites);
  vm.runInContext(extract('async function loadJmFavorites(', 'async function loadLibrary('), favorites);
  await favorites.loadJmFavorites();
  failSecond = true;
  await assert.rejects(favorites.loadJmFavorites({ page: 2 }), /Temporary/);
  assert.equal(favorites.state.comics.length, 1, 'Later page failure must preserve the first page');
  failSecond = false;
  await favorites.loadJmFavorites({ page: 2 });
  assert.equal(favorites.state.comics.length, 2);
  await favorites.loadJmFavorites();
  assert.equal(favorites.state.comics.length, 2, 'Background shelf refresh must preserve loaded favorite pages');
  assert.equal(favorites.state.jmFavorites.page, 2);
  console.log('Functional frontend regressions passed.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
