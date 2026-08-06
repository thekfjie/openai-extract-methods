import {
  Bot,
  CreditCard,
  FolderOpen,
  LayoutDashboard,
  Mail,
  RefreshCw,
  Settings,
  Wrench,
  Zap,
} from 'lucide-react';

export const NAV_ITEMS = [
  {
    type: 'link',
    id: 'dashboard',
    label: '控制总览',
    title: '控制总览',
    to: '/',
    icon: LayoutDashboard,
    end: true,
  },
  {
    type: 'group',
    id: 'openai',
    label: 'OpenAI 流程',
    path: '/openai',
    icon: Bot,
    defaultSub: 'openai4',
    items: [
      { sub: 'openai7', label: 'OpenAI 7（GPT 注册机）' },
      { sub: 'openai6', label: 'OpenAI 6（AT Maker）' },
      { sub: 'openai5', label: 'OpenAI 5（API 环境监督）' },
      { sub: 'openai4', label: 'OpenAI 注册（有头）', aliases: ['openai1', 'sub2api'] },
      { sub: 'openai2', label: 'OpenAI 2（Pool Stats）' },
      { sub: 'openai3', label: 'OpenAI 3（Go Engine）' },
    ],
  },
  {
    type: 'group',
    id: 'grok',
    label: 'Grok 流程',
    path: '/grok',
    icon: Zap,
    tone: 'var(--warning-color)',
    defaultSub: 'ttk',
    items: [
      { sub: 'ttk', label: 'Grok TTK 并发控台' },
      { sub: 'grok2_panel', label: 'Grok 2 外部面板' },
      { sub: 'cpa_convert', label: 'CPA Device-Flow 转换' },
      { sub: 'signup', label: 'Grok 历史注册任务' },
    ],
  },
  {
    type: 'group',
    id: 'payments',
    label: '支付',
    path: '/payments',
    icon: CreditCard,
    defaultSub: 'extract',
    items: [
      { sub: 'extract', label: '提炼中心', to: '/payments/extract' },
      { sub: 'center', label: '支付中心', to: '/payments/center' },
    ],
  },
  {
    type: 'group',
    id: 'converters',
    label: '凭证与 Token 转换',
    path: '/converters',
    icon: RefreshCw,
    defaultSub: 'convert',
    items: [
      { sub: 'convert', label: 'Session 转换' },
      { sub: 'token', label: '提取 eyJ' },
      { sub: 'promo', label: '月优惠检测' },
    ],
  },
  {
    type: 'group',
    id: 'infra',
    label: '邮箱与手机基础设施',
    path: '/infrastructure',
    icon: Mail,
    defaultSub: 'email_queue',
    redirects: { apple_mail: '/apple_mail' },
    items: [
      { sub: 'email_queue', label: '邮箱队列与分配' },
      { sub: 'outlook_groups', label: 'Outlook 分组划转' },
      { sub: 'phone_pool', label: 'SMS 接码与号码池' },
    ],
  },
  {
    type: 'link',
    id: 'openai_mail_pool',
    label: 'OpenAI 邮箱池',
    title: 'Mail Opus OpenAI 待注册邮箱池',
    to: '/openai-mail-pool',
    icon: Mail,
  },
  {
    type: 'link',
    id: 'apple_mail',
    label: 'Apple Mail',
    title: 'Apple Mail 控制台',
    to: '/apple_mail',
    icon: Mail,
  },
  {
    type: 'link',
    id: 'file_library',
    label: '文件库 / 素材库',
    title: '文件库 / 素材库',
    to: '/file-library',
    icon: FolderOpen,
  },
  {
    type: 'group',
    id: 'tools',
    label: '工具与探针',
    path: '/tools',
    icon: Wrench,
    defaultSub: 'cpa_monitor',
    items: [
      { sub: 'test_profiles', label: '多国测试资料' },
      { sub: 'outlook_register', label: 'Outlook 注册机' },
      { sub: 'cpa_monitor', label: 'CPA 状态探针' },
      { sub: 'sub2api_monitor', label: 'Sub2API 状态探针' },
      { sub: 'grok_results', label: 'Grok 结果分析' },
    ],
  },
  {
    type: 'group',
    id: 'settings',
    label: '系统设置',
    path: '/settings',
    icon: Settings,
    defaultSub: 'main_settings',
    items: [
      { sub: 'appearance', label: '外观与布局' },
      { sub: 'main_settings', label: '主服务与代理' },
      { sub: 'app_settings', label: 'App Console 参数' },
      { sub: 'purchase_settings', label: 'SMS 接码与 API' },
      { sub: 'traffic', label: '流量与请求历史' },
    ],
  },
];

const EXTRA_PAGE_TITLES = {
  '/workflows': '流程设计器 (Workflow Studio)',
};

function pathMatches(pathname, path) {
  return pathname === path || pathname.startsWith(`${path}/`);
}

export function groupForPath(pathname) {
  return NAV_ITEMS.find((item) => item.type === 'group' && pathMatches(pathname, item.path)) || null;
}

export function canonicalSub(group, requestedSub) {
  if (!group) return requestedSub || '';
  if (!requestedSub) return group.defaultSub;
  const direct = group.items.find((item) => item.sub === requestedSub);
  if (direct) return direct.sub;
  const aliased = group.items.find((item) => item.aliases?.includes(requestedSub));
  return aliased?.sub || group.defaultSub;
}

export function activeSubForLocation(group, pathname, search = '') {
  if (!group) return '';
  const pathItem = group.items.find((item) => item.to && pathMatches(pathname, item.to));
  if (pathItem) return pathItem.sub;
  return canonicalSub(group, new URLSearchParams(search).get('sub'));
}

export function navigationTitle(pathname, search = '') {
  const direct = NAV_ITEMS.find((item) => item.type === 'link' && item.to === pathname);
  if (direct) return direct.title || direct.label;

  const group = groupForPath(pathname);
  if (group) {
    const requestedSub = new URLSearchParams(search).get('sub');
    const redirectTarget = group.redirects?.[requestedSub];
    if (redirectTarget) return navigationTitle(redirectTarget);
    const activeSub = activeSubForLocation(group, pathname, search);
    return group.items.find((item) => item.sub === activeSub)?.label || group.label;
  }

  if (pathname.startsWith('/workflows/')) return '工作流详情';
  return EXTRA_PAGE_TITLES[pathname] || 'AutoMyAI 流程控制台';
}

export function sidebarExpandedForPath(pathname) {
  return pathname === '/';
}

export function targetExpandsSidebar(target) {
  return target.split('?')[0] === '/';
}
