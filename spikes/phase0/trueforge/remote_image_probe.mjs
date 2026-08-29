import { RemoteMCP } from '@truefoundry/trueforge-core/core';

const logger = {
  child() {
    return this;
  },
  debug() {},
  error() {},
  info() {},
  warn() {},
};

const remote = new RemoteMCP({
  id: 'phase0-facade',
  name: 'phase0-facade',
  url: process.env.TFY_PHASE0_MCP_URL,
  headers: {
    Authorization: `Bearer ${process.env.TFY_PHASE0_BEARER}`,
    Origin: process.env.TFY_PHASE0_ORIGIN,
  },
  logger,
  requestTimeoutMs: 10_000,
  connectTimeoutMs: 5_000,
  signal: AbortSignal.timeout(15_000),
});

const response = await remote.callTool({ name: 'inspect_asset', arguments: {} });
if ('authRequired' in response) {
  throw new Error('unexpected authentication challenge');
}
const blocks = response.result.content;
const images = blocks.filter(block => block.type === 'image');
if (images.length !== 1 || images[0].mimeType !== 'image/png') {
  throw new Error('expected one PNG image content block');
}
const image = Buffer.from(images[0].data, 'base64');
if (image.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' || image.readUInt32BE(16) !== 160 || image.readUInt32BE(20) !== 120) {
  throw new Error('unexpected image dimensions');
}
console.log(JSON.stringify({ image_blocks: images.length, mime_type: images[0].mimeType, image_bytes: image.length }));
