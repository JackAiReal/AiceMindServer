import { defineEventHandler, readBody, setResponseStatus } from 'h3';
import { extendMemberExpire } from '~/utils/member-users';
import { verifyAccessToken } from '~/utils/jwt-utils';
import {
  unAuthorizedResponse,
  useResponseError,
  useResponseSuccess,
} from '~/utils/response';

export default defineEventHandler(async (event) => {
  const userinfo = verifyAccessToken(event);
  if (!userinfo) {
    return unAuthorizedResponse(event);
  }

  const body = await readBody<Record<string, any>>(event);
  const id = String(body?.id || '').trim();
  const days = Number(body?.days || 0);

  if (!id || !Number.isFinite(days) || days <= 0) {
    setResponseStatus(event, 400);
    return useResponseError('id 或 days 参数不正确');
  }

  const updated = extendMemberExpire(id, days);
  if (!updated) {
    setResponseStatus(event, 404);
    return useResponseError('用户不存在');
  }

  return useResponseSuccess(updated);
});
