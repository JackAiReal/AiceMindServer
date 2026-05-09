<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue';

import { requestClient } from '#/api/request';

import {
  Button as AButton,
  Card as ACard,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Switch as ASwitch,
} from 'ant-design-vue';

interface EmailSettings {
  smtpHost: string;
  smtpPort: number;
  smtpUsername: string;
  smtpPassword: string;
  fromEmail: string;
  fromName: string;
  useTLS: boolean;
  useSSL: boolean;
  verifySubjectTemplate: string;
  verifyBodyTemplate: string;
}

const DEFAULT_SUBJECT_TEMPLATE = '【{{app_name}}】邮箱验证码';
const DEFAULT_BODY_TEMPLATE = `你好，{{nickname_or_email}}：

你正在注册 {{app_name}}，本次验证码为：{{code}}
验证码在 {{expire_minutes}} 分钟内有效，请勿泄露给他人。

请求邮箱：{{email}}
发送时间：{{now}}

如果这不是你的操作，请忽略本邮件。
{{app_name}} 团队`;

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const testEmail = ref('');

const formState = reactive<EmailSettings>({
  smtpHost: '',
  smtpPort: 465,
  smtpUsername: '',
  smtpPassword: '',
  fromEmail: '',
  fromName: 'AiceMind',
  useTLS: false,
  useSSL: true,
  verifySubjectTemplate: DEFAULT_SUBJECT_TEMPLATE,
  verifyBodyTemplate: DEFAULT_BODY_TEMPLATE,
});

const loadSettings = async () => {
  loading.value = true;
  try {
    const data = await requestClient.get<EmailSettings>('/system/email-settings');
    Object.assign(formState, data || {});
    if (!formState.verifySubjectTemplate) {
      formState.verifySubjectTemplate = DEFAULT_SUBJECT_TEMPLATE;
    }
    if (!formState.verifyBodyTemplate) {
      formState.verifyBodyTemplate = DEFAULT_BODY_TEMPLATE;
    }
  } finally {
    loading.value = false;
  }
};

const onSave = async (silent = false) => {
  saving.value = true;
  try {
    await requestClient.post('/system/email-settings/save', {
      ...formState,
    });
    if (!silent) {
      message.success('邮箱设置已保存');
    }
  } finally {
    saving.value = false;
  }
};

const onSendTestEmail = async () => {
  const email = testEmail.value.trim().toLowerCase();
  if (!email) {
    message.error('请先输入测试邮箱');
    return;
  }

  testing.value = true;
  try {
    await requestClient.post('/system/email-settings/send-test', {
      ...formState,
      testEmail: email,
    });
    message.success(`测试邮件已发送到 ${email}`);
  } finally {
    testing.value = false;
  }
};

onMounted(() => {
  void loadSettings();
});
</script>

<template>
  <div class="email-settings-page">
    <ACard :loading="loading" title="邮箱设置" :bordered="false">
      <AForm layout="vertical">
        <AFormItem label="SMTP Host" required>
          <AInput v-model:value="formState.smtpHost" placeholder="例如：smtp.qq.com" />
        </AFormItem>

        <AFormItem label="SMTP Port" required>
          <AInputNumber
            v-model:value="formState.smtpPort"
            :min="1"
            :max="65535"
            style="width: 100%"
          />
        </AFormItem>

        <AFormItem label="SMTP 用户名" required>
          <AInput v-model:value="formState.smtpUsername" placeholder="一般为发件邮箱" />
        </AFormItem>

        <AFormItem label="SMTP 密码 / 授权码" required>
          <AInput
            v-model:value="formState.smtpPassword"
            type="password"
            placeholder="请填写 SMTP 授权密码"
          />
        </AFormItem>

        <AFormItem label="发件邮箱" required>
          <AInput
            v-model:value="formState.fromEmail"
            placeholder="例如：noreply@yourdomain.com"
          />
        </AFormItem>

        <AFormItem label="发件人名称">
          <AInput v-model:value="formState.fromName" placeholder="例如：AiceMind 系统" />
        </AFormItem>

        <AFormItem label="安全配置">
          <div class="switch-row">
            <div class="switch-item">
              <span>启用 SSL</span>
              <ASwitch v-model:checked="formState.useSSL" />
            </div>
            <div class="switch-item">
              <span>启用 TLS</span>
              <ASwitch v-model:checked="formState.useTLS" />
            </div>
          </div>
        </AFormItem>

        <AFormItem label="验证码邮件标题模板" required>
          <AInput
            v-model:value="formState.verifySubjectTemplate"
            placeholder="例如：【{{app_name}}】邮箱验证码"
          />
        </AFormItem>

        <AFormItem label="验证码邮件正文模板" required>
          <AInput.TextArea
            v-model:value="formState.verifyBodyTemplate"
            :rows="10"
            placeholder="可使用模板变量，例如：{{code}}"
          />
          <div class="template-hint">
            可用占位变量：
            <code v-pre>{{app_name}}</code>
            <code v-pre>{{nickname_or_email}}</code>
            <code v-pre>{{code}}</code>
            <code v-pre>{{expire_minutes}}</code>
            <code v-pre>{{email}}</code>
            <code v-pre>{{now}}</code>
          </div>
        </AFormItem>

        <AFormItem>
          <div class="action-row">
            <AButton type="primary" :loading="saving" @click="onSave(false)">
              保存设置
            </AButton>

            <div class="test-mail-row">
              <AInput
                v-model:value="testEmail"
                placeholder="输入测试邮箱，例如 test@example.com"
                style="width: 320px"
              />
              <AButton :loading="testing" @click="onSendTestEmail">
                发送测试邮件
              </AButton>
            </div>
          </div>
        </AFormItem>
      </AForm>
    </ACard>
  </div>
</template>

<style scoped>
.email-settings-page {
  padding: 16px;
}

.switch-row {
  display: flex;
  gap: 24px;
}

.switch-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.template-hint {
  margin-top: 8px;
  color: #666;
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.template-hint code {
  background: #f5f5f5;
  border-radius: 4px;
  padding: 2px 6px;
}

.action-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.test-mail-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
