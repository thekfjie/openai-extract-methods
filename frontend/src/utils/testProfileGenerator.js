export const TEST_PROFILE_SCHEMA = 'automyai.test-profile.v2';
export const MAX_TEST_PROFILE_BATCH = 100;

const TEST_EMAIL_DOMAINS = [
  'icloud.example.test',
  'gmail.example.test',
  'outlook.example.test',
  'yahoo.example.test',
  'hotmail.example.test',
];

// These are published PayPal/Braintree sandbox test cards, not generated PANs.
// They pass ordinary card-brand and Luhn checks but are not live instruments.
const BRAINTREE_SANDBOX_CARDS = Object.freeze({
  JP: Object.freeze([
    Object.freeze({ brand: 'JCB', number: '3530111333300000' }),
    Object.freeze({ brand: 'Visa', number: '4111111111111111' }),
  ]),
  BR: Object.freeze([
    Object.freeze({ brand: 'Visa', number: '4111111111111111' }),
    Object.freeze({ brand: 'Mastercard', number: '5555555555554444' }),
  ]),
  US: Object.freeze([
    Object.freeze({ brand: 'Visa', number: '4012000033330620' }),
    Object.freeze({ brand: 'Mastercard', number: '5555555555554444' }),
  ]),
  GB: Object.freeze([
    Object.freeze({ brand: 'Visa', number: '4444333322221111' }),
    Object.freeze({ brand: 'Mastercard', number: '5555444433331111' }),
  ]),
  TR: Object.freeze([
    Object.freeze({ brand: 'Visa', number: '4111111111111111' }),
    Object.freeze({ brand: 'Mastercard', number: '2223000048400011' }),
  ]),
});

function hashSeed(value) {
  let hash = 2166136261;
  const source = String(value ?? '');
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function createRandom(seed) {
  let state = hashSeed(seed) || 0x6d2b79f5;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(random, values) {
  return values[Math.floor(random() * values.length)];
}

function randomInt(random, min, max) {
  return Math.floor(random() * (max - min + 1)) + min;
}

function pad(value, length) {
  return String(value).padStart(length, '0');
}

function shuffle(random, values) {
  const result = [...values];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const target = randomInt(random, 0, index);
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

function createPassword(random) {
  const upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  const lower = 'abcdefghijkmnopqrstuvwxyz';
  const digits = '23456789';
  const symbols = '!@#$%&*';
  const all = upper + lower + digits + symbols;
  const length = randomInt(random, 14, 18);
  const chars = [
    pick(random, upper),
    pick(random, lower),
    pick(random, digits),
    pick(random, symbols),
  ];
  while (chars.length < length) chars.push(pick(random, all));
  return shuffle(random, chars).join('');
}

function createBirthday(random, format) {
  const year = randomInt(random, 1974, 2002);
  const month = randomInt(random, 1, 12);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const day = randomInt(random, 1, lastDay);
  if (format === 'yyyy/mm/dd') return `${year}/${pad(month, 2)}/${pad(day, 2)}`;
  if (format === 'mm/dd/yyyy') return `${pad(month, 2)}/${pad(day, 2)}/${year}`;
  if (format === 'dd.mm.yyyy') return `${pad(day, 2)}.${pad(month, 2)}.${year}`;
  return `${pad(day, 2)}/${pad(month, 2)}/${year}`;
}

function profileSuffix(seed, country, index) {
  return hashSeed(`${seed}|${country}|${index}`).toString(36).toUpperCase().padStart(7, '0').slice(0, 7);
}

function latinSlug(value) {
  return String(value || 'test')
    .replace(/[\u0131İ]/g, 'i')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '.')
    .replace(/^\.|\.$/g, '') || 'test';
}

function isLuhnValid(value) {
  const digits = String(value || '').replace(/\D/g, '');
  if (digits.length < 12) return false;
  let sum = 0;
  let doubleDigit = false;
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    let digit = Number(digits[index]);
    if (doubleDigit) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    doubleDigit = !doubleDigit;
  }
  return sum % 10 === 0;
}

function formatCardNumber(value) {
  return String(value || '').replace(/\D/g, '').replace(/(.{4})/g, '$1 ').trim();
}

function createSandboxPayment(random, country) {
  const catalog = BRAINTREE_SANDBOX_CARDS[country] || BRAINTREE_SANDBOX_CARDS.US;
  const card = pick(random, catalog);
  const cvv = pick(random, ['123', '456', '789', '999']);
  const expiryMonth = pad(randomInt(random, 1, 12), 2);
  const expiryYear = randomInt(random, 31, 34);
  return {
    cardProvider: 'PayPal Braintree Sandbox',
    cardBrand: card.brand,
    cardNumber: formatCardNumber(card.number),
    expDate: `${expiryMonth}/${expiryYear}`,
    cvv,
    cardLuhnValid: isLuhnValid(card.number),
  };
}

const JP_LAST_NAMES = [
  { native: 'サトウ', latin: 'sato' },
  { native: 'スズキ', latin: 'suzuki' },
  { native: 'タカハシ', latin: 'takahashi' },
  { native: 'タナカ', latin: 'tanaka' },
  { native: 'ワタナベ', latin: 'watanabe' },
  { native: 'イトウ', latin: 'ito' },
  { native: 'ヤマモト', latin: 'yamamoto' },
  { native: 'ナカムラ', latin: 'nakamura' },
];
const JP_FIRST_NAMES = [
  { native: 'ハルト', latin: 'haruto' },
  { native: 'ユウト', latin: 'yuto' },
  { native: 'ソウタ', latin: 'sota' },
  { native: 'レン', latin: 'ren' },
  { native: 'アオイ', latin: 'aoi' },
  { native: 'ヒナ', latin: 'hina' },
  { native: 'ユイ', latin: 'yui' },
  { native: 'サクラ', latin: 'sakura' },
];
const JP_LOCATIONS = [
  { region: '東京都', city: '新宿区', postalCodes: ['160-0022', '160-0023', '169-0074'], towns: ['新宿', '西新宿', '北新宿'] },
  { region: '大阪府', city: '大阪市北区', postalCodes: ['530-0001', '530-0011', '530-0012'], towns: ['梅田', '大深町', '芝田'] },
  { region: '神奈川県', city: '横浜市西区', postalCodes: ['220-0004', '220-0011', '220-0012'], towns: ['北幸', '高島', 'みなとみらい'] },
  { region: '北海道', city: '札幌市中央区', postalCodes: ['060-0001', '060-0042', '060-0061'], towns: ['北一条西', '大通西', '南一条西'] },
  { region: '福岡県', city: '福岡市博多区', postalCodes: ['812-0011', '812-0012', '812-0038'], towns: ['博多駅前', '博多駅東', '祇園町'] },
];

const BR_FIRST_NAMES = ['Ana', 'Lucas', 'Mariana', 'João', 'Gabriel', 'Beatriz', 'Rafael', 'Camila'];
const BR_LAST_NAMES = ['Silva', 'Santos', 'Oliveira', 'Souza', 'Pereira', 'Costa', 'Rodrigues', 'Almeida'];
const BR_LOCATIONS = [
  { region: 'São Paulo (SP)', city: 'São Paulo', postalCodes: ['01001-000', '01310-100', '01426-001'], streets: ['Avenida Paulista', 'Rua Augusta', 'Rua Oscar Freire', 'Alameda Santos'] },
  { region: 'Rio de Janeiro (RJ)', city: 'Rio de Janeiro', postalCodes: ['20040-020', '22041-001', '22410-003'], streets: ['Avenida Rio Branco', 'Rua Barata Ribeiro', 'Rua Visconde de Pirajá'] },
  { region: 'Minas Gerais (MG)', city: 'Belo Horizonte', postalCodes: ['30130-000', '30140-071', '30180-100'], streets: ['Avenida Afonso Pena', 'Rua da Bahia', 'Avenida Getúlio Vargas'] },
  { region: 'Paraná (PR)', city: 'Curitiba', postalCodes: ['80010-000', '80240-000', '80420-090'], streets: ['Rua XV de Novembro', 'Avenida Sete de Setembro', 'Rua Comendador Araújo'] },
  { region: 'Pernambuco (PE)', city: 'Recife', postalCodes: ['50010-000', '51020-000', '52011-000'], streets: ['Avenida Conde da Boa Vista', 'Rua do Bom Jesus', 'Avenida Boa Viagem'] },
];

const US_FIRST_NAMES = ['James', 'Michael', 'Daniel', 'Thomas', 'Emily', 'Olivia', 'Sophia', 'Charlotte'];
const US_LAST_NAMES = ['Smith', 'Johnson', 'Williams', 'Brown', 'Wilson', 'Miller', 'Davis', 'Anderson'];
const US_LOCATIONS = [
  { region: 'CA', city: 'Los Angeles', areaCode: '213', postalCodes: ['90026', '90028', '90036'], streets: ['Sunset Boulevard', 'Wilshire Boulevard', 'Melrose Avenue'] },
  { region: 'NY', city: 'New York', areaCode: '212', postalCodes: ['10001', '10003', '10011'], streets: ['West 34th Street', 'Madison Avenue', 'Lexington Avenue'] },
  { region: 'IL', city: 'Chicago', areaCode: '312', postalCodes: ['60601', '60605', '60611'], streets: ['North Michigan Avenue', 'West Madison Street', 'South State Street'] },
  { region: 'TX', city: 'Austin', areaCode: '512', postalCodes: ['78701', '78703', '78704'], streets: ['Congress Avenue', 'South Lamar Boulevard', 'Guadalupe Street'] },
  { region: 'WA', city: 'Seattle', areaCode: '206', postalCodes: ['98101', '98102', '98109'], streets: ['Pike Street', '1st Avenue', 'Westlake Avenue'] },
  { region: 'FL', city: 'Miami', areaCode: '305', postalCodes: ['33130', '33131', '33137'], streets: ['Brickell Avenue', 'Biscayne Boulevard', 'Coral Way'] },
];

const GB_FIRST_NAMES = ['Oliver', 'George', 'Harry', 'Jack', 'Thomas', 'Emily', 'Olivia', 'Amelia'];
const GB_LAST_NAMES = ['Smith', 'Jones', 'Taylor', 'Brown', 'Wilson', 'Davies', 'Robinson', 'Evans'];
const GB_LOCATIONS = [
  { region: 'England', city: 'London', postalCodes: ['NW1 6XE', 'W1D 1BS', 'SW3 4UD'], streets: ['Baker Street', 'Oxford Street', "King's Road"] },
  { region: 'England', city: 'Manchester', postalCodes: ['M3 2BW', 'M1 7ED', 'M1 3BE'], streets: ['Deansgate', 'Oxford Road', 'Portland Street'] },
  { region: 'England', city: 'Birmingham', postalCodes: ['B2 4QA', 'B1 2HF', 'B4 6TB'], streets: ['New Street', 'Broad Street', 'Corporation Street'] },
  { region: 'Scotland', city: 'Edinburgh', postalCodes: ['EH2 2ER', 'EH2 2PF', 'EH1 1SG'], streets: ['Princes Street', 'George Street', 'Royal Mile'] },
  { region: 'Wales', city: 'Cardiff', postalCodes: ['CF10 1EP', 'CF10 2HE', 'CF11 9HB'], streets: ['Queen Street', 'Westgate Street', 'Cathedral Road'] },
];

const TR_FIRST_NAMES = ['Ahmet', 'Mehmet', 'Mustafa', 'Emre', 'Ayşe', 'Elif', 'Zeynep', 'Ceren'];
const TR_LAST_NAMES = ['Yılmaz', 'Kaya', 'Demir', 'Şahin', 'Çelik', 'Yıldız', 'Aydın', 'Arslan'];
const TR_LOCATIONS = [
  { region: 'İstanbul', city: 'Kadıköy', postalCodes: ['34710', '34714', '34718'], districts: ['Caferağa Mahallesi', 'Moda Mahallesi', 'Koşuyolu Mahallesi'], streets: ['Bağdat Caddesi', 'Moda Caddesi', 'Söğütlüçe Caddesi'] },
  { region: 'Ankara', city: 'Çankaya', postalCodes: ['06420', '06520', '06680'], districts: ['Kızılay Mahallesi', 'Bahçelievler Mahallesi', 'Kavaklıdere Mahallesi'], streets: ['Atatürk Bulvarı', 'Tunalı Hilmi Caddesi', 'Arjantin Caddesi'] },
  { region: 'İzmir', city: 'Konak', postalCodes: ['35210', '35220', '35240'], districts: ['Alsancak Mahallesi', 'Göztepe Mahallesi', 'Konak Mahallesi'], streets: ['Kıbrıs Şehitleri Caddesi', 'Mithatpaşa Caddesi', 'Cumhuriyet Bulvarı'] },
  { region: 'Bursa', city: 'Nilüfer', postalCodes: ['16120', '16130', '16285'], districts: ['Görükle Mahallesi', 'İhsaniye Mahallesi', 'Ataevler Mahallesi'], streets: ['Fatih Sultan Mehmet Bulvarı', 'Cevizli Cadde', 'Uğur Mumcu Bulvarı'] },
  { region: 'Antalya', city: 'Muratpaşa', postalCodes: ['07100', '07160', '07230'], districts: ['Lara Mahallesi', 'Fener Mahallesi', 'Şirinyalı Mahallesi'], streets: ['Tekelioğlu Caddesi', 'Eski Lara Yolu', 'İsmet Gökşen Caddesi'] },
];

function generateJpProfile({ random }) {
  const last = pick(random, JP_LAST_NAMES);
  const first = pick(random, JP_FIRST_NAMES);
  const location = pick(random, JP_LOCATIONS);
  const houseNumber = `${randomInt(random, 1, 9)}-${randomInt(random, 1, 30)}-${randomInt(random, 1, 25)}`;
  const street = `${pick(random, location.towns)}${houseNumber}`;
  const postalCode = pick(random, location.postalCodes);
  return {
    firstName: first.native,
    lastName: last.native,
    latinFirstName: first.latin,
    latinLastName: last.latin,
    fullName: `${last.native} ${first.native}`,
    postalCode,
    houseNumber,
    region: location.region,
    city: location.city,
    street,
    fullAddress: `〒${postalCode} ${location.region}${location.city}${street}`,
    phone: `000-0000-${pad(randomInt(random, 0, 9999), 4)}`,
  };
}

function generateBrProfile({ random }) {
  const firstName = pick(random, BR_FIRST_NAMES);
  const lastName = pick(random, BR_LAST_NAMES);
  const location = pick(random, BR_LOCATIONS);
  const houseNumber = String(randomInt(random, 10, 4999));
  const street = `${pick(random, location.streets)}, ${houseNumber}`;
  const postalCode = pick(random, location.postalCodes);
  return {
    firstName,
    lastName,
    latinFirstName: firstName,
    latinLastName: lastName,
    fullName: `${firstName} ${lastName}`,
    postalCode,
    houseNumber,
    region: location.region,
    city: location.city,
    street,
    fullAddress: `${street}, ${location.city} - ${location.region}, CEP ${postalCode}`,
    phone: `(00) 00000-${pad(randomInt(random, 0, 9999), 4)}`,
    nationalIdLabel: 'CPF（故意无效）',
    nationalId: '000.000.000-00',
  };
}

function generateUsProfile({ random }) {
  const firstName = pick(random, US_FIRST_NAMES);
  const lastName = pick(random, US_LAST_NAMES);
  const location = pick(random, US_LOCATIONS);
  const houseNumber = String(randomInt(random, 100, 9999));
  let street = `${houseNumber} ${pick(random, location.streets)}`;
  if (random() < 0.42) {
    const unitType = pick(random, ['Apt', 'Suite', 'Unit', '#']);
    const unit = random() < 0.5 ? String(randomInt(random, 1, 999)) : `${randomInt(random, 1, 30)}${pick(random, ['A', 'B', 'C', 'D'])}`;
    street += `, ${unitType} ${unit}`;
  }
  const postalCode = pick(random, location.postalCodes);
  return {
    firstName,
    lastName,
    latinFirstName: firstName,
    latinLastName: lastName,
    fullName: `${firstName} ${lastName}`,
    postalCode,
    houseNumber,
    region: location.region,
    city: location.city,
    street,
    fullAddress: `${street}, ${location.city}, ${location.region} ${postalCode}, USA`,
    phone: `(${location.areaCode}) 555-${pad(randomInt(random, 100, 199), 4)}`,
  };
}

function generateGbProfile({ random }) {
  const firstName = pick(random, GB_FIRST_NAMES);
  const lastName = pick(random, GB_LAST_NAMES);
  const location = pick(random, GB_LOCATIONS);
  const houseNumber = String(randomInt(random, 1, 199));
  let street = `${houseNumber} ${pick(random, location.streets)}`;
  if (random() < 0.38) street = `Flat ${randomInt(random, 1, 30)}, ${street}`;
  const postalCode = pick(random, location.postalCodes);
  return {
    firstName,
    lastName,
    latinFirstName: firstName,
    latinLastName: lastName,
    fullName: `${firstName} ${lastName}`,
    postalCode,
    houseNumber,
    region: location.region,
    city: location.city,
    street,
    fullAddress: `${street}, ${location.city}, ${postalCode}, United Kingdom`,
    phone: `07700 900${pad(randomInt(random, 0, 999), 3)}`,
  };
}

function generateTrProfile({ random }) {
  const firstName = pick(random, TR_FIRST_NAMES);
  const lastName = pick(random, TR_LAST_NAMES);
  const location = pick(random, TR_LOCATIONS);
  const houseNumber = String(randomInt(random, 1, 199));
  const apartment = String(randomInt(random, 1, 40));
  const street = `${pick(random, location.districts)}, ${pick(random, location.streets)} No:${houseNumber} Daire:${apartment}`;
  const postalCode = pick(random, location.postalCodes);
  return {
    firstName,
    lastName,
    latinFirstName: firstName,
    latinLastName: lastName,
    fullName: `${firstName} ${lastName}`,
    postalCode,
    houseNumber,
    region: location.region,
    city: location.city,
    street,
    fullAddress: `${street}, ${postalCode} ${location.city}/${location.region}, Türkiye`,
    phone: `+90 000 000 00 ${pad(randomInt(random, 0, 99), 2)}`,
    nationalIdLabel: 'T.C. Kimlik No（故意无效）',
    nationalId: '00000000000',
  };
}

// Country adapters are intentionally isolated here. Adding a country means
// registering one adapter with its labels and generator; the UI stays generic.
export const TEST_PROFILE_COUNTRY_REGISTRY = Object.freeze({
  JP: Object.freeze({
    code: 'JP', label: '日本', badge: 'JP', flag: '🇯🇵', birthdayFormat: 'yyyy/mm/dd',
    labels: { lastName: '姓（片假名）', firstName: '名（片假名）', postalCode: '邮编', region: '都道府県', city: '市区町村' },
    generate: generateJpProfile,
  }),
  BR: Object.freeze({
    code: 'BR', label: '巴西', badge: 'BR', flag: '🇧🇷', birthdayFormat: 'dd/mm/yyyy',
    labels: { lastName: '姓', firstName: '名', postalCode: 'CEP', region: '州', city: '城市' },
    generate: generateBrProfile,
  }),
  US: Object.freeze({
    code: 'US', label: '美国', badge: 'US', flag: '🇺🇸', birthdayFormat: 'mm/dd/yyyy',
    labels: { lastName: '姓', firstName: '名', postalCode: 'ZIP Code', region: '州', city: '城市' },
    generate: generateUsProfile,
  }),
  GB: Object.freeze({
    code: 'GB', label: '英国', badge: 'GB', flag: '🇬🇧', birthdayFormat: 'dd/mm/yyyy',
    labels: { lastName: '姓', firstName: '名', postalCode: 'Postcode', region: '地区', city: '城市' },
    generate: generateGbProfile,
  }),
  TR: Object.freeze({
    code: 'TR', label: '土耳其', badge: 'TR', flag: '🇹🇷', birthdayFormat: 'dd.mm.yyyy',
    labels: { lastName: '姓', firstName: '名', postalCode: 'Posta Kodu', region: '省 / 地区', city: '区 / 城市' },
    generate: generateTrProfile,
  }),
});

export const TEST_PROFILE_COUNTRIES = Object.values(TEST_PROFILE_COUNTRY_REGISTRY).map(({ code, label, flag }) => ({ code, label, flag }));

export function createTestProfileSeed() {
  const buffer = new Uint32Array(2);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(buffer);
  else {
    buffer[0] = Date.now() >>> 0;
    buffer[1] = Math.floor(Math.random() * 0xffffffff) >>> 0;
  }
  return `local-${buffer[0].toString(36)}-${buffer[1].toString(36)}`;
}

export function generateTestProfiles({ country = 'JP', count = 1, seed = 'automyai-local' } = {}) {
  const countryCode = String(country || 'JP').toUpperCase();
  const definition = TEST_PROFILE_COUNTRY_REGISTRY[countryCode];
  if (!definition) throw new Error(`Unsupported test profile country: ${countryCode}`);

  const normalizedSeed = String(seed || 'automyai-local').trim() || 'automyai-local';
  const normalizedCount = Math.max(1, Math.min(MAX_TEST_PROFILE_BATCH, Math.trunc(Number(count) || 1)));

  return Array.from({ length: normalizedCount }, (_, index) => {
    const random = createRandom(`${normalizedSeed}|${countryCode}|${index}`);
    const local = definition.generate({ random, index });
    const payment = createSandboxPayment(random, countryCode);
    const suffix = profileSuffix(normalizedSeed, countryCode, index);
    const profileId = `${countryCode}-TEST-${pad(index + 1, 3)}-${suffix}`;
    const emailFirst = latinSlug(local.latinFirstName || local.firstName);
    const emailLast = latinSlug(local.latinLastName || local.lastName);
    return {
      schema: TEST_PROFILE_SCHEMA,
      profileId,
      index: index + 1,
      country: countryCode,
      countryName: definition.label,
      synthetic: true,
      testOnly: true,
      seed: normalizedSeed,
      firstName: local.firstName,
      lastName: local.lastName,
      fullName: local.fullName,
      birthday: createBirthday(random, definition.birthdayFormat),
      postalCode: local.postalCode,
      houseNumber: local.houseNumber,
      region: local.region,
      city: local.city,
      street: local.street,
      fullAddress: local.fullAddress,
      phone: local.phone,
      email: `${emailFirst}.${emailLast}${randomInt(random, 10, 9999)}@${pick(random, TEST_EMAIL_DOMAINS)}`,
      password: createPassword(random),
      nationalIdLabel: local.nationalIdLabel || '证件号（不生成）',
      nationalId: local.nationalId || 'NOT-GENERATED',
      paymentTestReference: `TEST-${countryCode}-${suffix}`,
      ...payment,
      safety: {
        emailDeliverable: false,
        phoneCallable: false,
        nationalIdValid: false,
        paymentInstrumentValid: false,
        sandboxPaymentMethod: true,
        cardFormatValid: payment.cardLuhnValid,
      },
    };
  });
}

export function getTestProfileFields(profile) {
  const definition = TEST_PROFILE_COUNTRY_REGISTRY[profile?.country] || TEST_PROFILE_COUNTRY_REGISTRY.JP;
  return [
    { key: 'email', label: '邮箱', value: profile.email },
    { key: 'cardNumber', label: `卡号（${profile.cardBrand} Sandbox）`, value: profile.cardNumber, sandbox: true },
    { key: 'expDate', label: '有效期', value: profile.expDate, sandbox: true },
    { key: 'cvv', label: 'CVV', value: profile.cvv, sandbox: true },
    { key: 'lastName', label: definition.labels.lastName, value: profile.lastName },
    { key: 'firstName', label: definition.labels.firstName, value: profile.firstName },
    { key: 'postalCode', label: definition.labels.postalCode, value: profile.postalCode },
    { key: 'houseNumber', label: '门牌 / 号', value: profile.houseNumber },
    { key: 'region', label: definition.labels.region, value: profile.region },
    { key: 'city', label: definition.labels.city, value: profile.city },
    { key: 'street', label: '街道地址', value: profile.street },
    { key: 'fullAddress', label: '完整地址', value: profile.fullAddress, wide: true },
    { key: 'phone', label: '测试电话（不可拨通）', value: profile.phone },
    { key: 'password', label: '密码', value: profile.password },
    { key: 'birthday', label: '出生日期', value: profile.birthday },
    { key: 'nationalId', label: profile.nationalIdLabel, value: profile.nationalId, warning: true },
  ];
}

export function formatTestProfile(profile) {
  return getTestProfileFields(profile).map((field) => `${field.label}: ${field.value}`).join('\n');
}

export function testProfilesToCsv(profiles) {
  const columns = [
    ['profileId', 'profile_id'], ['country', 'country'], ['firstName', 'first_name'], ['lastName', 'last_name'],
    ['birthday', 'birthday'], ['email', 'email'], ['phone', 'phone'], ['postalCode', 'postal_code'],
    ['region', 'region'], ['city', 'city'], ['street', 'street'], ['fullAddress', 'full_address'],
    ['password', 'password'], ['nationalId', 'national_id_test_placeholder'],
    ['paymentTestReference', 'payment_test_reference'], ['cardProvider', 'card_test_provider'],
    ['cardBrand', 'card_brand'], ['cardNumber', 'card_sandbox_number'], ['expDate', 'card_expiry'], ['cvv', 'card_cvv'],
  ];
  const protectSpreadsheetCell = (value) => {
    const text = String(value ?? '');
    return /^[=+\-@]/.test(text) ? `'${text}` : text;
  };
  const escapeCell = (value) => `"${protectSpreadsheetCell(value).replace(/"/g, '""')}"`;
  const lines = [columns.map(([, label]) => escapeCell(label)).join(',')];
  profiles.forEach((profile) => {
    lines.push(columns.map(([key]) => escapeCell(profile[key])).join(','));
  });
  return `\ufeff${lines.join('\n')}`;
}
