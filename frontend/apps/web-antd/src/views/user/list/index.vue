<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';

import { requestClient } from '#/api/request';
import { listAccountsApi, type AccountItem } from '#/api/system/commerce';

import type { TableColumnsType } from 'ant-design-vue';
import {
  Button as AButton,
  DatePicker as ADatePicker,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  message,
  Modal,
  Modal as AModal,
  Radio as ARadio,
  Select as ASelect,
  Space as ASpace,
  Table as ATable,
  Tag as ATag,
} from 'ant-design-vue';

type MemberLevel = 'basic' | 'pro' | 'svip' | 'vip' | 'none';
type MemberStatus = 'active' | 'disabled' | 'expired' | 'inactive';

interface UserListRow {
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
  roles: string[];
  source: 'account';
}

const loading = ref(false);
const users = ref<UserListRow[]>([]);
const formOpen = ref(false);
const submitLoading = ref(false);
const editingId = ref<string>('');

const extendOpen = ref(false);
const extendLoading = ref(false);
const currentExtendUser = ref<UserListRow | null>(null);
const extendForm = reactive<{
  mode: 'days' | 'set';
  days: number;
  expireTime?: Dayjs;
}>({
  mode: 'days',
  days: 30,
  expireTime: undefined,
});

const ARadioGroup = ARadio.Group;

const formState = reactive<Partial<UserListRow>>({
  userNickname: '',
  userId: '',
  email: '',
  memberLevel: 'basic',
  memberStatus: 'active',
  startTime: '',
  expireTime: '',
  points: 0,
});

const levelOptions = [
  { label: '未开通', value: 'none' },
  { label: '基础版', value: 'basic' },
  { label: 'Pro', value: 'pro' },
  { label: 'VIP', value: 'vip' },
  { label: 'SVIP', value: 'svip' },
];

const statusOptions = [
  { label: '激活', value: 'active' },
  { label: '禁用', value: 'disabled' },
  { label: '过期', value: 'expired' },
  { label: '未开通', value: 'inactive' },
];

const columns: TableColumnsType<UserListRow> = [
  { title: '用户昵称', dataIndex: 'userNickname', key: 'userNickname' },
  { title: '用户ID', dataIndex: 'userId', key: 'userId' },
  { title: '邮箱', dataIndex: 'email', key: 'email' },
  { title: '角色', key: 'roles' },
  { title: '会员等级', dataIndex: 'memberLevel', key: 'memberLevel' },
  { title: '会员状态', key: 'memberStatus' },
  { title: '开始时间', dataIndex: 'startTime', key: 'startTime' },
  { title: '过期时间', dataIndex: 'expireTime', key: 'expireTime' },
  { title: '积分', dataIndex: 'points', key: 'points' },
  { title: '操作', key: 'actions', width: 320 },
];

const isEdit = computed(() => !!editingId.value);
const currentExpireText = computed(
  () => currentExtendUser.value?.expireTime || '-',
);

const normalizeRowFromAccount = (row: AccountItem): UserListRow => {
  const entitlement = (row.entitlement || {}) as Record<string, any>;
  const rawLevel = String(entitlement.level || 'none').toLowerCase();
  const rawStatus = String(entitlement.status || 'inactive').toLowerCase();

  const memberLevel: MemberLevel = ['basic', 'pro', 'vip', 'svip'].includes(rawLevel)
    ? (rawLevel as MemberLevel)
    : 'none';

  const memberStatus: MemberStatus = ['active', 'disabled', 'expired'].includes(rawStatus)
    ? (rawStatus as MemberStatus)
    : memberLevel === 'none'
      ? 'inactive'
      : 'inactive';

  return {
    id: row.id,
    userNickname: row.realName || row.username || '-',
    userId: row.username || '-',
    email: row.email || '-',
    memberLevel,
    memberStatus,
    startTime: String(entitlement.start_at || ''),
    expireTime: String(entitlement.expire_at || ''),
    points: Number(entitlement.points || 0),
    updatedAt: row.updatedAt || row.createdAt || '',
    roles: Array.isArray(row.roles) ? row.roles : [],
    source: 'account',
  };
};

const loadUsers = async () => {
  loading.value = true;
  try {
    const data = await listAccountsApi();
    users.value = (data || []).map(normalizeRowFromAccount);
  } finally {
    loading.value = false;
  }
};

const resetForm = () => {
  editingId.value = '';
  formState.userNickname = '';
  formState.userId = '';
  formState.email = '';
  formState.memberLevel = 'basic';
  formState.memberStatus = 'active';
  formState.startTime = '';
  formState.expireTime = '';
  formState.points = 0;
};

const onCreate = () => {
  resetForm();
  formOpen.value = true;
};

const onEdit = (row: UserListRow) => {
  editingId.value = row.id;
  formState.userNickname = row.userNickname;
  formState.userId = row.userId;
  formState.email = row.email;
  formState.memberLevel = row.memberLevel === 'none' ? 'basic' : row.memberLevel;
  formState.memberStatus = row.memberStatus === 'inactive' ? 'active' : row.memberStatus;
  formState.startTime = row.startTime;
  formState.expireTime = row.expireTime;
  formState.points = row.points;
  formOpen.value = true;
};

const onSubmit = async () => {
  if (!formState.userNickname || !formState.userId) {
    message.error('用户昵称和用户ID必填');
    return;
  }

  submitLoading.value = true;
  try {
    if (isEdit.value) {
      await requestClient.post('/system/member/update', {
        id: editingId.value,
        ...formState,
      });
      message.success('会员信息已更新');
    } else {
      await requestClient.post('/system/member/create', {
        ...formState,
      });
      message.success('会员信息已新增');
    }

    formOpen.value = false;
    await loadUsers();
  } finally {
    submitLoading.value = false;
  }
};

const onToggleStatus = async (row: UserListRow) => {
  const nextStatus: MemberStatus =
    row.memberStatus === 'active' ? 'disabled' : 'active';
  await requestClient.post('/system/member/toggle-status', {
    id: row.id,
    status: nextStatus,
  });
  message.success(nextStatus === 'active' ? '已激活' : '已禁用');
  await loadUsers();
};

const onExtendExpire = (row: UserListRow) => {
  currentExtendUser.value = row;
  extendForm.mode = 'days';
  extendForm.days = 30;
  extendForm.expireTime = row?.expireTime ? dayjs(row.expireTime) : undefined;
  extendOpen.value = true;
};

const onConfirmExtend = async () => {
  if (!currentExtendUser.value) return;

  extendLoading.value = true;
  try {
    if (extendForm.mode === 'set') {
      if (!extendForm.expireTime) {
        message.error('请选择指定过期时间');
        return;
      }

      const expireTime = extendForm.expireTime.format('YYYY-MM-DD HH:mm:ss');
      await requestClient.post('/system/member/extend-expire', {
        id: currentExtendUser.value.id,
        expireTime,
      });
      message.success(`已更新过期时间为 ${expireTime}`);
    } else {
      const days = Number(extendForm.days);
      if (!Number.isFinite(days) || days <= 0) {
        message.error('请输入正确的延长天数');
        return;
      }

      await requestClient.post('/system/member/extend-expire', {
        id: currentExtendUser.value.id,
        days,
      });
      message.success(`已延长 ${days} 天`);
    }

    extendOpen.value = false;
    currentExtendUser.value = null;
    await loadUsers();
  } finally {
    extendLoading.value = false;
  }
};

const onDelete = (row: UserListRow) => {
  Modal.confirm({
    title: '确认删除该会员记录？',
    content: `${row.userNickname} (${row.userId})`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await requestClient.delete(`/system/member/${row.id}`);
      message.success('删除成功');
      await loadUsers();
    },
  });
};

const memberStatusText = (status: MemberStatus) => {
  if (status === 'active') return '激活';
  if (status === 'disabled') return '禁用';
  if (status === 'expired') return '过期';
  return '未开通';
};

const memberStatusColor = (status: MemberStatus) => {
  if (status === 'active') return 'green';
  if (status === 'disabled') return 'orange';
  if (status === 'expired') return 'red';
  return 'default';
};

onMounted(() => {
  void loadUsers();
});
</script>

<template>
  <div class="user-list-page">
    <div class="page-header">
      <div>
        <h2>用户列表</h2>
        <p class="page-tip">这里展示的是已注册账号，同时兼容显示会员状态，避免出现“能登录但列表里找不到”的情况。</p>
      </div>
      <AButton type="primary" @click="onCreate">新增会员</AButton>
    </div>

    <ATable
      :loading="loading"
      :data-source="users"
      :columns="columns"
      row-key="id"
      :pagination="{ pageSize: 10 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'roles'">
          <ASpace wrap>
            <ATag v-for="role in record.roles" :key="role">{{ role }}</ATag>
            <span v-if="!record.roles?.length">-</span>
          </ASpace>
        </template>

        <template v-else-if="column.key === 'memberStatus'">
          <ATag :color="memberStatusColor(record.memberStatus)">
            {{ memberStatusText(record.memberStatus) }}
          </ATag>
        </template>

        <template v-else-if="column.key === 'actions'">
          <ASpace>
            <AButton size="small" @click="onEdit(record)">编辑会员</AButton>
            <AButton
              size="small"
              :type="record.memberStatus === 'active' ? 'default' : 'primary'"
              @click="onToggleStatus(record)"
            >
              {{ record.memberStatus === 'active' ? '禁用' : '激活' }}
            </AButton>
            <AButton size="small" @click="onExtendExpire(record)">
              延长过期时间
            </AButton>
            <AButton danger size="small" @click="onDelete(record)">删除会员</AButton>
          </ASpace>
        </template>
      </template>
    </ATable>

    <AModal
      v-model:open="formOpen"
      :title="isEdit ? '编辑会员信息' : '新增会员信息'"
      :confirm-loading="submitLoading"
      @ok="onSubmit"
    >
      <AForm layout="vertical">
        <AFormItem label="用户昵称" required>
          <AInput v-model:value="formState.userNickname" />
        </AFormItem>
        <AFormItem label="用户ID" required>
          <AInput v-model:value="formState.userId" :disabled="isEdit" />
        </AFormItem>
        <AFormItem label="邮箱">
          <AInput v-model:value="formState.email" />
        </AFormItem>
        <AFormItem label="会员等级">
          <ASelect v-model:value="formState.memberLevel" :options="levelOptions" />
        </AFormItem>
        <AFormItem label="会员状态">
          <ASelect
            v-model:value="formState.memberStatus"
            :options="statusOptions"
          />
        </AFormItem>
        <AFormItem label="开始时间">
          <AInput
            v-model:value="formState.startTime"
            placeholder="YYYY-MM-DD HH:mm:ss"
          />
        </AFormItem>
        <AFormItem label="过期时间">
          <AInput
            v-model:value="formState.expireTime"
            placeholder="YYYY-MM-DD HH:mm:ss"
          />
        </AFormItem>
        <AFormItem label="积分数">
          <AInputNumber v-model:value="formState.points" :min="0" style="width: 100%" />
        </AFormItem>
      </AForm>
    </AModal>

    <AModal
      v-model:open="extendOpen"
      title="延长/修改激活时间"
      :confirm-loading="extendLoading"
      @ok="onConfirmExtend"
      @cancel="currentExtendUser = null; extendForm.expireTime = undefined"
    >
      <AForm layout="vertical">
        <AFormItem label="当前过期时间">
          <AInput :value="currentExpireText" disabled />
        </AFormItem>

        <AFormItem label="更新方式">
          <ARadioGroup v-model:value="extendForm.mode">
            <ARadio value="days">延长 X 天</ARadio>
            <ARadio value="set">修改为指定时间</ARadio>
          </ARadioGroup>
        </AFormItem>

        <AFormItem v-if="extendForm.mode === 'days'" label="延长天数">
          <AInputNumber
            v-model:value="extendForm.days"
            :min="1"
            :precision="0"
            style="width: 100%"
          />
        </AFormItem>

        <AFormItem v-else label="指定过期时间">
          <ADatePicker
            v-model:value="extendForm.expireTime"
            show-time
            format="YYYY-MM-DD HH:mm:ss"
            style="width: 100%"
          />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>

<style scoped>
.user-list-page {
  padding: 16px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 16px;
}

.page-header h2 {
  margin: 0;
  font-size: 18px;
}

.page-tip {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
}
</style>
