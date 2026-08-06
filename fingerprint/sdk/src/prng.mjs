import { createHash } from "node:crypto";

function hashWords(seed) {
  const digest = createHash("sha256").update(String(seed)).digest();
  return [0, 4, 8, 12].map((offset) => digest.readUInt32LE(offset));
}

export function createPrng(seed) {
  let [a, b, c, d] = hashWords(seed);

  function next() {
    const t = (b << 9) >>> 0;
    let r = Math.imul(a, 5);
    r = Math.imul(((r << 7) | (r >>> 25)) >>> 0, 9);
    c ^= a;
    d ^= b;
    b ^= c;
    a ^= d;
    c ^= t;
    d = ((d << 11) | (d >>> 21)) >>> 0;
    return (r >>> 0) / 0x1_0000_0000;
  }

  return {
    float(min = 0, max = 1) {
      return min + next() * (max - min);
    },
    int(min, max) {
      if (!Number.isInteger(min) || !Number.isInteger(max) || max < min) {
        throw new TypeError(`Invalid integer range: ${min}..${max}`);
      }
      return min + Math.floor(next() * (max - min + 1));
    },
    bool(probability = 0.5) {
      return next() < probability;
    },
    pick(values) {
      if (!Array.isArray(values) || values.length === 0) {
        throw new TypeError("Cannot pick from an empty list");
      }
      return values[this.int(0, values.length - 1)];
    },
    sample(values, count) {
      const copy = [...values];
      const size = Math.min(Math.max(0, count), copy.length);
      for (let index = copy.length - 1; index > 0; index -= 1) {
        const target = this.int(0, index);
        [copy[index], copy[target]] = [copy[target], copy[index]];
      }
      return copy.slice(0, size);
    },
    hex(length, uppercase = true) {
      const alphabet = uppercase ? "0123456789ABCDEF" : "0123456789abcdef";
      return Array.from({ length }, () => alphabet[this.int(0, 15)]).join("");
    }
  };
}

export function deriveSeed(seed, index) {
  return `${String(seed)}::${index}`;
}
