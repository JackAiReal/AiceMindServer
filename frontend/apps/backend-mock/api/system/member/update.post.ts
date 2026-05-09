import { defineEventHandler, readBody, setResponseStatus } from 'h3';
import { updateMemberUser } from '~/utils/member-users';
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
  if (!id) {
    setResponseStatus(event, 400);
    return useResponseError('id 不能为空');
  }

  const updated = updateMemberUser(id, body);
  if (!updated) {
    setResponseStatus(event, 404);
    return useResponseError('用户不存在');
  }

  return useResponseSuccess(updated);
});
