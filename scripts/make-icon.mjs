// Generates the tray icons as real PNG files so the repo carries no binaries.
// Runs automatically via the predev / prebuild npm hooks.
import { deflateSync } from 'node:zlib'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SIZE = 32

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c >>> 0
})

function crc32(buf) {
  let c = 0xffffffff
  for (const byte of buf) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function chunk(type, data) {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([len, body, crc])
}

function encodePng(width, height, rgba) {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8 // bit depth
  ihdr[9] = 6 // RGBA
  const scanlines = Buffer.alloc((width * 4 + 1) * height)
  for (let y = 0; y < height; y++) {
    scanlines[y * (width * 4 + 1)] = 0 // no filter
    rgba.copy(scanlines, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4)
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(scanlines, { level: 9 })),
    chunk('IEND', Buffer.alloc(0))
  ])
}

/** A filled clock face: ring in `color`, white hands pointing to 12 and 4. */
function drawClock(color) {
  const buf = Buffer.alloc(SIZE * SIZE * 4)
  const c = (SIZE - 1) / 2
  const put = (x, y, [r, g, b], a) => {
    const i = (y * SIZE + x) * 4
    buf[i] = r
    buf[i + 1] = g
    buf[i + 2] = b
    buf[i + 3] = Math.max(buf[i + 3], Math.round(a * 255))
  }

  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const d = Math.hypot(x - c, y - c)
      const edge = Math.min(1, Math.max(0, 15 - d)) // 1px antialiased edge
      if (edge > 0) put(x, y, color, edge)
    }
  }

  // Hands, drawn as short thick strokes from the centre.
  const stroke = (dx, dy, length) => {
    for (let t = 0; t <= length * 2; t++) {
      const x = Math.round(c + (dx * t) / 2)
      const y = Math.round(c + (dy * t) / 2)
      for (const [ox, oy] of [
        [0, 0],
        [1, 0],
        [0, 1]
      ]) {
        put(x + ox, y + oy, [255, 255, 255], 1)
      }
    }
  }
  stroke(0, -1, 9)
  stroke(0.72, 0.52, 7)

  return buf
}

const outDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'resources')
mkdirSync(outDir, { recursive: true })
writeFileSync(join(outDir, 'tray.png'), encodePng(SIZE, SIZE, drawClock([110, 125, 160])))
writeFileSync(join(outDir, 'tray-active.png'), encodePng(SIZE, SIZE, drawClock([52, 199, 123])))
console.log('Wrote resources/tray.png and resources/tray-active.png')
