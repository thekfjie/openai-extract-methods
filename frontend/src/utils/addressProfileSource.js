export const REMOTE_ADDRESS_COUNTRIES = Object.freeze([
  ['US', '美国', '🇺🇸'], ['BR', '巴西', '🇧🇷'], ['CA', '加拿大', '🇨🇦'], ['AU', '澳大利亚', '🇦🇺'],
  ['JP', '日本', '🇯🇵'], ['TW', '台湾', '🇹🇼'], ['KR', '韩国', '🇰🇷'],
  ['HK', '香港', '🇭🇰'], ['GB', '英国', '🇬🇧'], ['DE', '德国', '🇩🇪'],
  ['SG', '新加坡', '🇸🇬'], ['FR', '法国', '🇫🇷'], ['IT', '意大利', '🇮🇹'],
  ['ES', '西班牙', '🇪🇸'], ['NL', '荷兰', '🇳🇱'], ['MY', '马来西亚', '🇲🇾'],
  ['RU', '俄罗斯', '🇷🇺'], ['CN', '中国', '🇨🇳'], ['TH', '泰国', '🇹🇭'],
  ['PH', '菲律宾', '🇵🇭'], ['AR', '阿根廷', '🇦🇷'], ['TR', '土耳其', '🇹🇷'],
  ['VN', '越南', '🇻🇳'],
].map(([code, label, flag]) => Object.freeze({ code, label, flag })));

export const REMOTE_ADDRESS_FIELD_LABELS = Object.freeze({
  Address: '街道地址（源站原文）',
  Trans_Address: '街道地址（转写）',
  Trans_Cn_Address: '街道地址（中文）',
  Full_Address_Combined: '完整地址（本地组合）',
  Full_Name: '姓名',
  Full_Name_Tran: '姓名（转写）',
  Birthday: '出生日期',
  Gender: '性别',
  Title: '称谓',
  Telephone: '电话',
  Fax: '传真',
  City: '城市',
  State: '州 / 省',
  State_Full: '地区全称',
  Zip_Code: '邮编',
  Username: '用户名',
  Password: '密码',
  Temporary_mail: '临时邮箱',
  Educational_Background: '教育背景',
  Occupation: '职业',
  Employment_Status: '就业状态',
  Monthly_Salary: '月收入',
  Company_Size: '公司规模',
  Company_Name: '公司名称',
  Industry: '行业',
  Blood_Type: '血型',
  Height: '身高',
  Weight: '体重',
  Hair_Color: '发色',
  System: '系统',
  Browser_User_Agent: '浏览器 UA',
  Website: '网站',
  Security_Question: '安全问题',
  Security_Answer: '安全答案',
  Credit_Card_Type: '信用卡类型',
  Credit_Card_Number: '信用卡号',
  CVV2: 'CVV2',
  Expires: '有效期',
  Social_Security_Number: '证件号 / SSN',
});

export const REMOTE_ADDRESS_FIELD_ORDER = Object.freeze([
  'Temporary_mail', 'Full_Name', 'Full_Name_Tran', 'Zip_Code', 'State', 'State_Full',
  'City', 'Address', 'Trans_Address', 'Trans_Cn_Address', 'Full_Address_Combined', 'Telephone', 'Password', 'Birthday',
  'Gender', 'Title', 'Username', 'Fax',
  'Educational_Background', 'Occupation', 'Employment_Status', 'Monthly_Salary',
  'Company_Size', 'Company_Name', 'Industry', 'Blood_Type', 'Height', 'Weight', 'Hair_Color',
  'System', 'Browser_User_Agent', 'Website', 'Security_Question', 'Security_Answer',
  'Credit_Card_Type', 'Credit_Card_Number', 'CVV2', 'Expires', 'Social_Security_Number',
]);

export function remoteAddressFieldLabel(key) {
  return REMOTE_ADDRESS_FIELD_LABELS[key] || key;
}

export function remoteAddressFields(profile) {
  const sourceFields = profile?.fields || {};
  const fields = { ...sourceFields };
  const addressParts = [
    sourceFields.Trans_Address || sourceFields.Trans_Cn_Address || sourceFields.Address,
    sourceFields.City,
    sourceFields.State_Full || sourceFields.State,
    sourceFields.Zip_Code,
  ].filter((value, index, values) => value && values.indexOf(value) === index);
  if (addressParts.length > 1) fields.Full_Address_Combined = addressParts.join(', ');
  const ordered = REMOTE_ADDRESS_FIELD_ORDER.filter((key) => fields[key]);
  const extras = Object.keys(fields).filter((key) => !ordered.includes(key)).sort();
  return [...ordered, ...extras].map((key) => ({
    key,
    label: remoteAddressFieldLabel(key),
    value: fields[key],
  }));
}

export function formatRemoteAddressProfile(profile) {
  if (!profile) return '';
  const country = profile.country?.label || profile.country?.code || '';
  const lines = [`来源: meiguodizhi.com`, `国家: ${country}`];
  remoteAddressFields(profile).forEach((field) => lines.push(`${field.label}: ${field.value}`));
  return lines.join('\n');
}
