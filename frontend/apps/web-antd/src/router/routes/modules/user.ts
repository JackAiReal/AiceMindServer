import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    name: 'UserManagement',
    path: '/user',
    meta: {
      icon: 'mdi:account-group',
      keepAlive: true,
      order: 100,
      title: '用户管理',
    },
    redirect: '/user/list',
    children: [
      {
        name: 'UserList',
        path: '/user/list',
        component: () => import('#/views/user/list/index.vue'),
        meta: {
          icon: 'mdi:account-multiple',
          title: '用户列表',
        },
      },
    ],
  },
];

export default routes;
