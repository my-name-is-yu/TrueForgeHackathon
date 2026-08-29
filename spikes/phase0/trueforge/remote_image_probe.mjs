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
    'X-Phase0-Image-Probe': '1',
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
const hostPathExposed = blocks.some(block => Object.entries(block).some(([key, value]) => {
  if (key === 'data') return false;
  if (key === 'path' || key === 'filePath' || key === 'hostPath') return typeof value === 'string';
  return typeof value === 'string' && (value.startsWith('/') || value.startsWith('file://') || /^[A-Za-z]:[\\/]/.test(value));
}));
const width = image.readUInt32BE(16);
const height = image.readUInt32BE(20);
if (image.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' || width !== 160 || height !== 120) {
  throw new Error('unexpected image dimensions');
}
console.log(JSON.stringify({
  image_blocks: images.length,
  mime_type: images[0].mimeType,
  width,
  height,
  host_path_exposed: hostPathExposed,
}));
