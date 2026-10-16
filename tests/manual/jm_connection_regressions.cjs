const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../public/app.js'), 'utf8');
const extract = (first, next) => source.slice(source.indexOf(first), source.indexOf(next, source.indexOf(first)));

(async () => {
  const timers = [];
  const home = {
    state: { activeCollection: 'jm-online', online: {} },
    renderOnlineHome() {}, remoteOnlineComic: (item) => ({ ...item }), clearTimeout() {},
    setTimeout(callback) { timers.push(callback); },
    api: async () => ({ refreshing: true, sections: [{ items: [{ albumId: '701' }] }] }),
  };
  vm.createContext(home);
  vm.runInContext(extract('async function loadOnlineHome(', 'async function browseOnlineCategory('), home);
  await home.loadOnlineHome();
  const card = home.state.online.homeSections[0].items[0];
  card.tags = ['hydrated'];
  card.metadataLoaded = true;
  card.metadataRetries = 2;
  await home.loadOnlineHome({ poll: true });
  assert.equal(home.state.online.homeSections[0].items[0], card, 'Polling an unchanged snapshot must preserve the hydrated card');
  assert.deepEqual(card.tags, ['hydrated']);

  const node = { classList: { remove() {} } };
  const recommendations = {
    state: { activeCollection: 'library', online: { recommendationFilterRevision: 0,
      excludedRecommendationTags: [], recommendationBatch: 0, recommendationSort: 'diverse' } },
    $: () => node, URLSearchParams, setBusy() {}, renderOnlineRecommendations() {},
    remoteOnlineComic: (item) => ({ ...item }), clearTimeout() {},
    setTimeout() { throw new Error('Hidden recommendations must not keep polling'); },
    api: async () => ({ refreshing: true, items: [{ albumId: '701' }] }), toast() {},
  };
  vm.createContext(recommendations);
  vm.runInContext(extract('async function loadOnlineRecommendations(', 'async function searchOnline('), recommendations);
  await recommendations.loadOnlineRecommendations();
  const recommended = recommendations.state.online.recommendations[0];
  recommended.tags = ['loaded'];
  await recommendations.loadOnlineRecommendations({ poll: true });
  assert.equal(recommendations.state.online.recommendations[0], recommended);

  let complete;
  const stale = {
    onlineRequestPrefix: 'test', AbortController,
    state: { online: { metadataRevision: 0, metadataControllers: new Map() } },
    api: () => new Promise((resolve) => { complete = resolve; }),
    onlineItems() { throw new Error('Cancelled response must not mutate current cards'); },
  };
  vm.createContext(stale);
  vm.runInContext(extract('async function hydrateOnlineMetadataOne(', 'function pumpOnlineMetadataQueue('), stale);
  const request = stale.hydrateOnlineMetadataOne('701', true);
  stale.state.online.metadataRevision++;
  complete({ items: [{ albumId: '701', detailsComplete: true, tags: ['old'] }] });
  await request;
  assert.equal(stale.state.online.metadataControllers.size, 0);

  vm.runInContext(extract('function needsOnlineMetadata(', 'async function hydrateOnlineMetadataOne('), stale);
  const failed = { sourceId: '701', metadataFailed: true, metadataRetries: 3, metadataFailedAt: Date.now() };
  assert.equal(stale.needsOnlineMetadata(failed), false);
  failed.metadataFailedAt -= 121000;
  assert.equal(stale.needsOnlineMetadata(failed), true, 'A later visit must be able to retry a transient failure');
  assert.equal(failed.metadataRetries, 0);

  let delay;
  const polling = {
    state: { downloads: [], caches: [] },
    loadDownloads: async () => {}, loadCaches: async () => {},
    setTimeout(_callback, milliseconds) { delay = milliseconds; },
  };
  vm.createContext(polling);
  vm.runInContext(extract('function pollTaskQueues()', 'async function init()'), polling);
  polling.pollTaskQueues();
  await new Promise(setImmediate);
  assert.equal(delay, 10000);
  polling.state.downloads = [{ status: 'running' }];
  polling.pollTaskQueues();
  await new Promise(setImmediate);
  assert.equal(delay, 1100);
  console.log('JM connection frontend regressions passed.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
