<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';
import type { Recordable } from '@vben/types';

import { computed, markRaw, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { AuthenticationLogin, SliderCaptcha, z } from '@vben/common-ui';
import { $t } from '@vben/locales';
import { preferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';

import {
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  message,
} from 'ant-design-vue';

import { getAccessCodesApi, getUserInfoApi } from '#/api/core';
import { requestClient } from '#/api/request';
import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();
const accessStore = useAccessStore();
const userStore = useUserStore();
const router = useRouter();
const route = useRoute();

const registerMode = ref(false);
const registerLoading = ref(false);
const sendCodeLoading = ref(false);
const sendCodeCountdown = ref(0);
let countdownTimer: null | number = null;

const registerForm = reactive({
  email: '',
  code: '',
  nickname: '',
  password: '',
  confirmPassword: '',
  inviteCode: '',
  captchaPassed: false,
});

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      component: 'VbenInput',
      componentProps: {
        placeholder: '请输入账号或邮箱',
      },
      fieldName: 'username',
      label: '账号 / 邮箱',
      rules: z.string().min(1, { message: '请输入账号或邮箱' }),
    },
    {
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: $t('authentication.passwordTip'),
      },
      fieldName: 'password',
      label: $t('authentication.password'),
      rules: z.string().min(1, { message: $t('authentication.passwordTip') }),
    },
    {
      component: markRaw(SliderCaptcha),
      fieldName: 'captcha',
      rules: z.boolean().refine((value) => value, {
        message: '请完成滑块验证',
      }),
    },
  ];
});

const canSendCode = computed(() => sendCodeCountdown.value <= 0);

function stopCountdown() {
  if (countdownTimer) {
    window.clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function startCountdown() {
  sendCodeCountdown.value = 60;
  stopCountdown();
  countdownTimer = window.setInterval(() => {
    if (sendCodeCountdown.value <= 1) {
      sendCodeCountdown.value = 0;
      stopCountdown();
      return;
    }
    sendCodeCountdown.value -= 1;
  }, 1000);
}

function resetRegisterForm() {
  registerForm.email = '';
  registerForm.code = '';
  registerForm.nickname = '';
  registerForm.password = '';
  registerForm.confirmPassword = '';
  registerForm.inviteCode = '';
  registerForm.captchaPassed = false;
}

function switchToRegister() {
  resetRegisterForm();
  stopCountdown();
  sendCodeCountdown.value = 0;
  registerMode.value = true;
}

function switchToLogin() {
  registerMode.value = false;
}

function onLoginSubmit(values: Recordable<any>) {
  void authStore.authLogin({
    username: values.username,
    password: values.password,
  });
}

async function onSendCode() {
  const email = registerForm.email.trim().toLowerCase();
  if (!email) {
    message.error('请先填写邮箱');
    return;
  }
  if (!registerForm.captchaPassed) {
    message.error('请先完成滑块验证');
    return;
  }

  sendCodeLoading.value = true;
  try {
    await requestClient.post('/auth/send-email-code', { email });
    message.success('验证码已发送，请查收邮箱');
    startCountdown();
  } finally {
    sendCodeLoading.value = false;
  }
}

async function onRegisterSubmit() {
  const email = registerForm.email.trim().toLowerCase();
  const code = registerForm.code.trim();
  const nickname = registerForm.nickname.trim();
  const password = registerForm.password;
  const confirmPassword = registerForm.confirmPassword;
  const inviteCode = registerForm.inviteCode.trim();

  if (!email || !code || !nickname || !password || !confirmPassword) {
    message.error('请完整填写注册信息');
    return;
  }
  if (!registerForm.captchaPassed) {
    message.error('请先完成滑块验证');
    return;
  }
  if (password.length < 6) {
    message.error('密码长度至少 6 位');
    return;
  }
  if (password !== confirmPassword) {
    message.error('两次输入的密码不一致');
    return;
  }

  registerLoading.value = true;
  try {
    const result = await requestClient.post<{ accessToken: string }>('/auth/register', {
      email,
      code,
      nickname,
      password,
      confirmPassword,
      inviteCode: inviteCode || undefined,
    });

    const accessToken = result?.accessToken;
    if (!accessToken) {
      message.error('注册成功但未返回登录令牌');
      return;
    }

    accessStore.setAccessToken(accessToken);

    const [userInfo, accessCodes] = await Promise.all([
      getUserInfoApi(),
      getAccessCodesApi(),
    ]);

    userStore.setUserInfo(userInfo);
    accessStore.setAccessCodes(accessCodes);
    accessStore.setLoginExpired(false);

    message.success('注册成功，已自动登录');
    registerMode.value = false;

    await router.push(userInfo.homePath || preferences.app.defaultHomePath);
  } finally {
    registerLoading.value = false;
  }
}

onMounted(() => {
  if (String(route.query.mode || '').toLowerCase() === 'register') {
    switchToRegister();
  }
});

onBeforeUnmount(() => {
  stopCountdown();
});
</script>

<template>
  <div class="login-page-wrap">
    <AuthenticationLogin
      v-if="!registerMode"
      :form-schema="formSchema"
      :loading="authStore.loginLoading"
      :show-code-login="false"
      :show-qrcode-login="false"
      :show-third-party-login="false"
      :show-register="false"
      @submit="onLoginSubmit"
    >
      <template #to-register>
        <div class="mt-3 flex items-center justify-center gap-1 text-sm">
          <span>还没有账号?</span>
          <AButton type="link" size="small" @click="switchToRegister">
            创建账号
          </AButton>
        </div>
      </template>
    </AuthenticationLogin>

    <div v-else class="register-panel">
      <div class="register-title">创建账号 🚀</div>
      <div class="register-sub-title">使用邮箱验证码快速注册</div>

      <AForm layout="vertical">
        <AFormItem label="邮箱" required>
          <AInput v-model:value="registerForm.email" placeholder="请输入可接收验证码的邮箱" />
        </AFormItem>

        <AFormItem label="邮箱验证码" required>
          <div class="code-input-row">
            <AInput v-model:value="registerForm.code" placeholder="请输入 6 位验证码" />
            <AButton :disabled="!canSendCode" :loading="sendCodeLoading" @click="onSendCode">
              {{ canSendCode ? '发送验证码' : `${sendCodeCountdown}s` }}
            </AButton>
          </div>
        </AFormItem>

        <AFormItem label="昵称" required>
          <AInput v-model:value="registerForm.nickname" placeholder="注册成功后显示的昵称" />
        </AFormItem>

        <AFormItem label="密码" required>
          <AInput
            v-model:value="registerForm.password"
            type="password"
            placeholder="请输入至少 6 位密码"
          />
        </AFormItem>

        <AFormItem label="确认密码" required>
          <AInput
            v-model:value="registerForm.confirmPassword"
            type="password"
            placeholder="请再次输入密码"
          />
        </AFormItem>

        <AFormItem label="邀请码（选填)">
          <AInput v-model:value="registerForm.inviteCode" placeholder="没有可留空" />
        </AFormItem>

        <AFormItem label="滑块验证" required>
          <SliderCaptcha v-model="registerForm.captchaPassed" />
        </AFormItem>

        <div class="register-actions">
          <AButton type="primary" :loading="registerLoading" @click="onRegisterSubmit">
            完成注册
          </AButton>
          <AButton @click="switchToLogin">返回登录</AButton>
        </div>
      </AForm>
    </div>
  </div>
</template>

<style scoped>
.login-page-wrap {
  width: 100%;
}

.register-panel {
  width: 100%;
}

.register-title {
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 6px;
}

.register-sub-title {
  color: rgba(0, 0, 0, 0.45);
  margin-bottom: 18px;
}

.code-input-row {
  display: flex;
  gap: 8px;
}

.register-actions {
  display: flex;
  gap: 8px;
}
</style>
