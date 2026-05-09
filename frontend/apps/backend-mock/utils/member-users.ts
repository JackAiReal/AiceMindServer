export type MemberLevel = 'basic' | 'pro' | 'svip' | 'vip';
export type MemberStatus = 'active' | 'disabled' | 'expired';

export interface MemberUser {
  id: string;
  userNickname: string;
  userId: string;
  email: string;
  memberLevel: MemberLevel;
  memberStatus: MemberStatus;
  startTime: string;
  expireTime: string;
  points: number;
  updatedAt: string;
}

const now = () => new Date();
const toDateTime = (date: Date) => {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

const addDays = (base: Date, days: number) => {
  const d = new Date(base);
  d.setDate(d.getDate() + days);
  return d;
};

const makeUser = (
  userNickname: string,
  userId: string,
  email: string,
  memberLevel: MemberLevel,
  memberStatus: MemberStatus,
  points: number,
  startOffsetDays: number,
  expireOffsetDays: number,
): MemberUser => {
  const current = now();
  return {
    id: crypto.randomUUID(),
    userNickname,
    userId,
    email,
    memberLevel,
    memberStatus,
    startTime: toDateTime(addDays(current, startOffsetDays)),
    expireTime: toDateTime(addDays(current, expireOffsetDays)),
    points,
    updatedAt: toDateTime(current),
  };
};

const seed: MemberUser[] = [
  makeUser('超级管理员', 'superadmin', 'superadmin@aicemind.com', 'svip', 'active', 9999, -30, 365),
  makeUser('测试用户A', 'user_a', 'usera@example.com', 'pro', 'active', 1200, -10, 50),
  makeUser('测试用户B', 'user_b', 'userb@example.com', 'vip', 'disabled', 800, -40, -1),
];

export let MEMBER_USERS: MemberUser[] = [...seed];

export function listMemberUsers() {
  return [...MEMBER_USERS].sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
}

export function createMemberUser(payload: Partial<MemberUser>) {
  const current = now();
  const item: MemberUser = {
    id: crypto.randomUUID(),
    userNickname: String(payload.userNickname || '').trim() || '未命名用户',
    userId: String(payload.userId || '').trim() || `user_${Math.random().toString(36).slice(2, 8)}`,
    email: String(payload.email || '').trim() || '',
    memberLevel: (payload.memberLevel as MemberLevel) || 'basic',
    memberStatus: (payload.memberStatus as MemberStatus) || 'active',
    startTime: String(payload.startTime || '').trim() || toDateTime(current),
    expireTime: String(payload.expireTime || '').trim() || toDateTime(addDays(current, 30)),
    points: Number(payload.points || 0),
    updatedAt: toDateTime(current),
  };
  MEMBER_USERS.unshift(item);
  return item;
}

export function updateMemberUser(id: string, payload: Partial<MemberUser>) {
  const idx = MEMBER_USERS.findIndex((item) => item.id === id);
  if (idx < 0) return null;

  const old = MEMBER_USERS[idx]!;
  const next: MemberUser = {
    ...old,
    userNickname: String(payload.userNickname ?? old.userNickname).trim(),
    userId: String(payload.userId ?? old.userId).trim(),
    email: String(payload.email ?? old.email).trim(),
    memberLevel: (payload.memberLevel as MemberLevel) || old.memberLevel,
    memberStatus: (payload.memberStatus as MemberStatus) || old.memberStatus,
    startTime: String(payload.startTime ?? old.startTime).trim(),
    expireTime: String(payload.expireTime ?? old.expireTime).trim(),
    points: Number(payload.points ?? old.points),
    updatedAt: toDateTime(now()),
  };

  MEMBER_USERS[idx] = next;
  return next;
}

export function setMemberStatus(id: string, status: MemberStatus) {
  return updateMemberUser(id, { memberStatus: status });
}

export function extendMemberExpire(id: string, days: number) {
  const idx = MEMBER_USERS.findIndex((item) => item.id === id);
  if (idx < 0) return null;

  const target = MEMBER_USERS[idx]!;
  const base = new Date(target.expireTime.replace(' ', 'T'));
  const validBase = Number.isNaN(base.getTime()) ? now() : base;
  const nextDate = addDays(validBase, days);
  return updateMemberUser(id, { expireTime: toDateTime(nextDate) });
}

export function deleteMemberUser(id: string) {
  const len = MEMBER_USERS.length;
  MEMBER_USERS = MEMBER_USERS.filter((item) => item.id !== id);
  return MEMBER_USERS.length !== len;
}
