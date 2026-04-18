function parseDataUrl(dataUrl) {
  if (typeof dataUrl !== 'string') {
    throw new Error('imageData must be a data URL string');
  }

  const match = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
  if (!match) {
    throw new Error('Unsupported data URL format (expected data:*;base64,...)');
  }

  const mime = match[1];
  const b64 = match[2];
  const buffer = Buffer.from(b64, 'base64');
  return { mime, buffer };
}

function bufferToDataUrl(buffer, mime) {
  const base64 = buffer.toString('base64');
  return `data:${mime};base64,${base64}`;
}

module.exports = {
  parseDataUrl,
  bufferToDataUrl,
};

