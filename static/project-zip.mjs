/* Small, uncompressed ZIP writer for the fixed list of course source files. */
export function makeZip(files) {
  const encoder = new TextEncoder(), entries = [], directory = [];
  let offset = 0, directorySize = 0;
  for (const [filename, content] of Object.entries(files)) {
    const name = encoder.encode(filename), body = encoder.encode(content);
    let crc = -1;
    for (const byte of body) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (-(crc & 1) & 0xedb88320);
    }
    crc ^= -1;
    const local = new Uint8Array(30 + name.length), l = new DataView(local.buffer);
    l.setUint32(0, 0x04034b50, true); l.setUint16(4, 20, true);
    l.setUint16(6, 0x800, true); l.setUint16(12, 33, true); l.setUint32(14, crc, true);
    l.setUint32(18, body.length, true); l.setUint32(22, body.length, true);
    l.setUint16(26, name.length, true); local.set(name, 30);
    const central = new Uint8Array(46 + name.length), c = new DataView(central.buffer);
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true);
    c.setUint16(8, 0x800, true); c.setUint16(14, 33, true); c.setUint32(16, crc, true);
    c.setUint32(20, body.length, true); c.setUint32(24, body.length, true);
    c.setUint16(28, name.length, true); c.setUint32(42, offset, true); central.set(name, 46);
    entries.push(local, body); directory.push(central);
    offset += local.length + body.length; directorySize += central.length;
  }
  const tail = new DataView(new ArrayBuffer(22));
  tail.setUint32(0, 0x06054b50, true); tail.setUint16(8, directory.length, true);
  tail.setUint16(10, directory.length, true); tail.setUint32(12, directorySize, true);
  tail.setUint32(16, offset, true);
  return new Blob([...entries, ...directory, tail.buffer], {type: "application/zip"});
}
