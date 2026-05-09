import { defineEventHandler, readBody, setResponseStatus } from 'h3';
import { createMemberUser } from '~/utils/member-users';
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
  if (!body?.userNickname || !body?.userId) {
    setResponseStatus(event, 400);
    return useResponseError('用户昵称和用户ID不能为空');
  }

  const created = createMemberUser(body);
  return useResponseSuccess(created);
});
