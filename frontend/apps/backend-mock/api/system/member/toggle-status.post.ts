import { defineEventHandler, readBody, setResponseStatus } from 'h3';
import { setMemberStatus } from '~/utils/member-users';
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
  const status = String(body?.status || '').trim();

  if (!id || !status) {
    setResponseStatus(event, 400);
    return useResponseError('id 或 status 缺失');
  }

  if (!['active', 'disabled', 'expired'].includes(status)) {
    setResponseStatus(event, 400);
    return useResponseError('status 无效');
  }

  const updated = setMemberStatus(id, status as any);
  if (!updated) {
    setResponseStatus(event, 404);
    return useResponseError('用户不存在');
  }

  return useResponseSuccess(updated);
});
