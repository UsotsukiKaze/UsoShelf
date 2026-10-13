const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../public/app.js'), 'utf8');
function extract(first, next) { return source.slice(source.indexOf(first), source.indexOf(next, source.indexOf(first))); }
const nodes = new Map();
const context = {
  state: { online: { recommendations: [], excludedRecommendationTags: ['Bad'], recommendationBatch: 0, recommendationBatchCount: 1 },
    readerPreview: false, readerComic: { id: 'chapter', progressPage: 69, pageCount: 80 }, readerResumePage: 69,
    readerPageIndex: 3, readerPages: [{ index: 0 }, { index: 1 }, { index: 2 }, { index: 3 }], readerMode: 'page' },
  $: (selector) => {
    if (selector === '#reader-stream-state') return null;
    if (!nodes.has(selector)) nodes.set(selector, { classList: { toggle() {}, add() {}, remove() {} }, innerHTML: '', insertAdjacentHTML() {}, addEventListener() {} });
    return nodes.get(selector);
  },
  $$: () => [],
  recommendationTagKey: (tag) => String(tag).toLowerCase(),
  formatSnapshotTime: () => '',
  onlineCardTemplate: (comic) => comic.id,
  bindOnlineImages() {}, renderRecommendationTagFilter() {}, hydrateOnlineMetadata() {}, renderOnlineSearchAssists() {},
  readerPageTemplate: (_comic, page) => String(page.index),
  updatePagedReader: () => {},
  requestAnimationFrame: (cb) => cb(),
  clearTimeout() {},
  api: () => { throw new Error('must not overwrite an unloaded bookmark'); },
};
vm.createContext(context);
vm.runInContext(extract('function renderOnlineRecommendations()', 'function replayAnimation'), context);
context.state.online.recommendations = [
  { id: 'forbidden', metadataLoaded: true, tags: ['BAD'] },
  { id: 'unknown', metadataLoaded: false, tags: [] },
  { id: 'allowed', metadataLoaded: true, tags: ['good'] },
];
context.renderOnlineRecommendations();
assert.equal(nodes.get('#online-recommend-grid').innerHTML, 'allowed');
vm.runInContext(extract('function saveReaderProgress(', 'function observeReaderPages'), context);
context.saveReaderProgress(3);
assert.equal(context.state.readerComic.progressPage, 69);
vm.runInContext(extract('function appendReaderPages(', 'function previewStreamMessage'), context);
context.appendReaderPages(context.state.readerComic, Array.from({ length: 72 }, (_, index) => ({ index })));
assert.equal(context.state.readerPageIndex, 69);
assert.equal(context.state.readerResumePage, null);
console.log('Frontend audit regressions passed.');
