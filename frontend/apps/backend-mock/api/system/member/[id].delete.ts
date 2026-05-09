import { defineEventHandler, getRouterParam, setResponseStatus } from 'h3';
import { deleteMemberUser } from '~/utils/member-users';
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

  const id = String(getRouterParam(event, 'id') || '').trim();
  if (!id) {
    setResponseStatus(event, 400);
    return useResponseError('id 不能为空');
  }

  const ok = deleteMemberUser(id);
  if (!ok) {
    setResponseStatus(event, 404);
    return useResponseError('用户不存在');
  }

  return useResponseSuccess(true);
});
