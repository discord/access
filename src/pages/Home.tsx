import React, {Fragment, ReactNode, useEffect} from 'react';
import {useLocation, useNavigate} from 'react-router-dom';

import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import Grid from '@mui/material/Grid';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import {alpha, useTheme} from '@mui/material';

import AdminIcon from '@mui/icons-material/ManageAccounts';
import AppOwnerIcon from '@mui/icons-material/AppShortcut';
import ExpiringGroupsIcon from '@mui/icons-material/RunningWithErrors';
import ExpiringRolesIcon from '@mui/icons-material/HeartBroken';
import HowToRegIcon from '@mui/icons-material/HowToReg';
import GroupRequestIcon from '@mui/icons-material/GroupAdd';
import RoleRequestIcon from '@mui/icons-material/WorkHistory';
import AccessRequestIcon from '../components/icons/MoreTime';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import GeneralIcon from '@mui/icons-material/AllInclusive';
import LinkIcon from '@mui/icons-material/Link';
import PeopleLeadIcon from '@mui/icons-material/ContentPaste';
import FAQIcon from '@mui/icons-material/TipsAndUpdates';
import UserIcon from '@mui/icons-material/AccountBox';

import {appName} from '../config/accessConfig';
import {useCurrentUser} from '../authentication';
import CreateAccessRequest from './requests/Create';
import CreateRoleRequest from './role_requests/Create';
import CreateGroupRequest from './group_requests/Create';
import {
  useGetRequests,
  useGetRoleRequests,
  useGetGroupRequests,
  useGetUserGroupAudits,
  useGetGroupRoleAudits,
} from '../api/apiComponents';

interface StatConfig {
  id: string;
  Icon: React.ComponentType<{sx?: object}>;
  label: string;
  singularLabel?: string;
  path?: string;
  color?: string;
  isAction?: boolean;
}

// Number of sections shown when the card is at full width
const INITIAL_SECTIONS = 5;

// Ordered list of stats that can appear in the summary bar.
// Items with a count of 0 are skipped; action items (isAction) always show.
const STAT_CONFIGS: StatConfig[] = [
  {
    id: 'role-requests',
    Icon: RoleRequestIcon,
    label: 'Pending role requests',
    singularLabel: 'Pending role request',
    path: '/role-requests?assignee_user_id=@me',
    color: '#0EA5E9',
  },
  {
    id: 'access-requests',
    Icon: AccessRequestIcon,
    label: 'Pending access requests',
    singularLabel: 'Pending access request',
    path: '/requests?assignee_user_id=@me',
    color: '#0EA5E9',
  },
  {
    id: 'group-requests',
    Icon: GroupRequestIcon,
    label: 'Pending group creation requests',
    singularLabel: 'Pending group creation request',
    path: '/group-requests?assignee_user_id=@me',
    color: '#0EA5E9',
  },
  {
    id: 'expiring-roles',
    Icon: ExpiringRolesIcon,
    label: 'Roles losing access soon',
    singularLabel: 'Role losing access soon',
    path: '/expiring-roles?owner_id=@me',
  },
  {
    id: 'expiring-access',
    Icon: ExpiringGroupsIcon,
    label: 'Users losing access soon',
    singularLabel: 'User losing access soon',
    path: '/expiring-groups?owner_id=@me',
  },
  {
    id: 'my-roles-expiring',
    Icon: HowToRegIcon,
    label: 'My roles losing access soon',
    singularLabel: 'My role losing access soon',
    path: '/expiring-roles?role_owner_id=@me',
  },
  {
    id: 'my-access-expiring',
    Icon: UserIcon,
    label: 'My access expiring soon',
    path: '/expiring-groups?user_id=@me',
  },
  {
    id: 'make-access-request',
    Icon: AccessRequestIcon,
    label: 'New access request',
    isAction: true,
  },
  {
    id: 'make-role-request',
    Icon: RoleRequestIcon,
    label: 'New role request',
    isAction: true,
  },
  {
    id: 'make-group-request',
    Icon: GroupRequestIcon,
    label: 'New group request',
    isAction: true,
  },
  {
    id: 'explore-user-docs',
    Icon: UserIcon,
    label: 'Explore user docs',
    isAction: true,
  },
  {
    id: 'explore-group-owner-docs',
    Icon: PeopleLeadIcon,
    label: 'Explore group owner docs',
    isAction: true,
  },
];

const sections: Record<string, [string, string, ReactNode]> = {
  // section shorthand --> [guide title, button title, icon]
  general: [`Welcome to ${appName}!`, 'Overview', <GeneralIcon />],
  users: ['Guide for All Users', 'All Users', <UserIcon />],
  'people-lead': ['Guide for Group and Role Owners', 'Group and Role Owners', <PeopleLeadIcon />],
  'app-owner': ['Guide for App Owners', 'App Owners', <AppOwnerIcon />],
  admin: ['Guide for Admins', 'Admins', <AdminIcon />],
  faq: ['FAQ', 'FAQ', <FAQIcon />],
};

const guide: Record<string, Record<string, string>> = {
  // section shorthand --> [question --> answer]
  // Use [[section--slug|label]] syntax in answers to create internal links.
  // Use ((url|label)) syntax in answers to create external links.
  general: {
    [`What is ${appName}?`]: `${appName} is a tool for managing who in your organization has access to different resources. It expands upon the feature set provided by Okta and was created to be transparent and discoverable and to enable employees to view and manage their own access.`,
    'Users, Groups, Roles, Apps': `Users are people in your organization. This may include, but is not limited to, employees and contractors. Users may be a member or owner of a group. \n\nThere are three types of groups in ${appName} with different features, namely 'vanilla' standalone groups, app groups, and roles, all of which map to Okta groups. \n\nRoles may be added as members or owners of app groups and standalone groups. When this happens, all members of the role are added as a member or owner of the group. \n\nApps are resources, such as third-party SaaS applications (eg. GitHub), or first-party services, like an internal administrator dashboard. Any app that is compatible with Okta can be an app within ${appName}. App groups can be tied to specific permissions associated with an app.`,
    'Access Requests': `Users can create access requests to join a group. Access requests must be approved by a group owner, an app owner (if it's an app group), or ${appName} administrator. The 'Access Requests' tab displays all access requests in your organization as well as your own requests and requests assigned to you. Access requests may also be created from that page with the Create Request button and dialog.`,
    'Role Requests': `Role owners can create role requests to ask that their roles are added to a group. Role requests must be approved by a group owner, an app owner (if it's an app group), or ${appName} administrator. The 'Role Requests' tab displays all role requests in your organization as well as your own requests and requests assigned to you. Role requests may also be created from that page with the Create Request button and dialog.`,
    'Group Requests': `All users can create group requests to ask that a group is created. Group requests must be approved by an app owner (if it's an app group) or ${appName} administrator. The 'Group Requests' tab displays all group requests in your organization as well as your own requests and requests assigned to you. Group requests may also be created from that page with the Create Request button and dialog.`,
    'Audit Pages':
      "Every user, group, and role has a corresponding audit page, which shows the access history for the entity. It can be viewed by clicking the clock-arrow icon next to the user/role/group name on the entity's page. Roles additionally have a 'Role audit' page that can be viewed by clicking the icon below the clock-arrow icon on a role page (it's similar to a Celtic knot). It displays the role's membership and ownership history.",
    'Tags and Constraints':
      "Tags can be used to label groups and apps (for their app groups) and, optionally, to apply constraints. These constraints include setting an ownership or membership time limit, requiring a reason for access, and disabling owners from adding themselves as members of a group. To view tags, click the 'Tags' button at the top of the 'Groups' or 'Apps' pages.",
    'Auto-approvals and other plugins': `${appName} uses the Python pluggy framework to allow for additional functionality to be added to the system. Some examples of this include adding a notification plugin to send emails, SMS, etc. when access requests are made and resolved and adding a conditional access plugin to automatically approve or deny requests if they match certain conditions. For more information and examples of plugins, see the ${appName} README at https://github.com/discord/access?tab=readme-ov-file#plugins.`,
    [`Learn more about ${appName}`]: `${appName} is open-sourced under the Apache 2.0 license. View the source code at ((https://github.com/discord/access|https://github.com/discord/access)) and check out our blog post that talks about the development process at ((https://dis.gd/access-blog|dis.gd/access-blog)).`,
  },
  users: {
    'Creating an access request':
      "You can create access requests for membership or ownership by clicking 'Request Membership' or 'Request Ownership' directly from the page for the group or role you need access to. Otherwise, navigate to the 'Access Requests' tab then click the 'Create Request' button at the top of the page. From there, you can select the group, duration, and provide a reason for the request. After submitting, the group owner(s) will be notified to approve or deny the request.",
    'View your access':
      'On the top right of any page, you can click the person icon to navigate to your user page. From there, you can see all of the groups you are an owner or member of.',
    "View someone else's access": `To view someone else's access, navigate to the 'Users' tab. From there, you can scroll through all of the people in your organization or search for a specific user's page. Alternatively, you can click the user's name almost anywhere it appears in ${appName} (on a group page, in an access request, etc.).`,
    'View a group or role':
      "The 'Groups' tab shows a list of all groups, app groups, and roles. Click on any row to view the group's details and who has access to the group. \n\nRoles may also be viewed separately by clicking the 'Roles' tab in the menu. In addition to showing owners and members, each role page displays the groups to which role membership grants access.",
    'View an app': `The 'Apps' tab displays a list of all apps connected to ${appName}. From there, you can select any row to view the details of the app and any app groups associated with the app.`,
    'View your access history':
      'From your user page, click the clock-arrow icon to the right of your name to navigate to your user audit page. There, you can see your ownership and membership history as well as additional details like who added or removed you from a group.',
    'View your expiring access':
      "Under the 'Expiring Groups' tab, select 'My Access' from the dropdown menu. On this page, you can see all of your access that is expiring soon, filter between active and inactive access, and look at your access within a specific timeframe (including in the past).",
    'Viewing tags':
      "A list of existing tags can be viewed by clicking the 'Tags' button at the top of the 'Groups' and 'Apps' pages. To view the details of a tag, click on the row you are interested in to see which groups the tag is applied to and any constraints it enforces.\n\nYou can also navigate to a tag's page by clicking on the tag on the page for an app or group that has the tag applied.",
    'Creating a group creation request':
      "Users can request that groups are created. To do so, navigate to the 'Group Requests' tab, then click the 'Create Request' button at the top of the page. From there, you can select the group type and associated app (if applicable), and fill out group details like name, description, and tags. After submitting the request, an admin or app owner (if the request is for an app group) will need to approve or reject the request.\n\nIf the request is approved, you (the requester) will be added as the owner of the new group.\n\nIf you are an app owner and request to create an app group for your app, it will be automatically approved.",
  },
  'people-lead': {
    'Group owners vs. role owners':
      'Generally, a role should be made up of individuals who share a job function and would likely need the same permissions for the same resources. As a result, role owners are typically people-leads since those are the people who have the most context about job functions.\n\nOn the other hand, standalone groups and app groups typically correspond to permissions associated with accessing data or a service. The owners of these groups should therefore usually be subject-matter experts (for example, system owners) for that group or app since they are the ones who have the most context on which permissions certain groups of employees need.',
    'What can group owners do?':
      'Group owners are responsible for managing groups. They are able to change the group name and description, add additional owners, and manage group memberships. They can also delete groups.',
    'Responding to an access or role request':
      "If your organization has the notification system set up, you will receive a notification when someone requests access to a group you own for themselves or on behalf of a role they own. Otherwise, you can see access and role requests for groups you own under 'Access Requests' > 'Assigned to Me' or 'Role Requests' > 'Assigned to Me'. \n\nFrom there, you can click 'View' to see the details of the access or role request, historical access, set an amount of time for access, provide a reason for approving or denying the request, and then either approve or reject the request.",
    'Creating a role request':
      "You can create role requests for membership or ownership of groups from the 'Role Requests' page. Click the 'Create Request' button at the top of the page to open the request creating dialog. From there, you can select the role for which you'd like to make the request (only roles you own will be shown), the group, the duration, and whether you'd like to request membership or ownership, and provide a reason for the request. After submitting, the group owner(s) will be notified to approve or deny the request.",
    'Managing a group':
      "Group owners are able to edit the group's name and description by clicking the pencil icon to the right of the group's name on its group page. Owners may also delete the group by selecting the garbage can icon to the right of the group name.\n\nGroup owners are also responsible for managing access to the group. This can be accomplished by approving access requests made for the group and by adding users or roles as owners and members from the group's page. With all groups, it is possible to grant temporary access, which will automatically expire after the set time.",
    'Managing expiring access for groups you own':
      "To view the users whose access is expiring soon for groups you own, navigate to 'Expiring Groups' > 'Owned by Me'. There are filters available to see access that expires during a specific time period, only active or inactive access, and access that has not been reviewed yet or all expring access.\n\nThe 'Bulk Renew' button at the top of the page opens the bulk renewal dialog. Here, you can provide a reason for renewing the access, set an amount of time for the renewal, and select whether or not you would like to renew access for each user. If your organization has notifications enabled, any access that is marked to allow expiration (ie. not renewing the access) will be omitted from subsequent expiring access notifications.\n\nTo view the roles that will be losing access to groups you own soon, navigate to 'Expiring Roles' > 'Owned Groups'. This page also has a bulk renewal dialog that functions in the same way as the one for renewing individual access.",
    'Managing expiring access for roles you own':
      "To view access that is expiring for roles that you own, navigate to 'Expiring Roles' > 'Owned Roles.' From there, you can see the access that will be expiring soon for roles you own and create a role request on behalf of the role if continued access is still needed.",
    'Blocked roles': `If a group is marked with a tag that has the 'Owner can't renew their own access' constraint enabled, you may be blocked from renewing a role's access to a group you own. This is generally due to you being both an owner and member of the role in question. To renew this role's access to the group, have either another group owner renew the role's access who is not a member of the role or have an ${appName} admin renew the role.`,
    'Group tags': `Group/role owners are able to apply existing tags to groups they own. If these tags are applied, any enabled tag constraints will be applied to the group. Only ${appName} administrators are able to remove tags.`,
  },
  'app-owner': {
    'What is an app owner?': `App owners are the owners of the App-<App name>-Owners group (members of this group have no implicit permissions for the App in ${appName}, but they may be useful in an upstream application that utilizes the group membership for permissions). App owners implicitly own all of the app's app groups. App owners are therefore able to edit the name and description for an app, add app tags, and manage any app group (see the 'Managing a group' section in the 'Group Owners' user guide).`,
    'What are the responsibilities of app owners?':
      'As an app owner, your main responsibility is to grant folks access to your app. This can be done by directly adding members or, preferably, roles to your app groups. If you want to delegate individuals or roles to be able to manage membership of specific app groups, you can do that by adding them as owners of the specified app group. As with other groups and roles, access can be configured to automatically expire as a means to provide temporary access to specific app groups. For example, if you have an app group only used infrequently that provides elevated permissions in the application, consider only granting access to this group temporarily as needed instead of indefinite standing access.',
    'Creating and managing groups for your app':
      'As an app owner, you own all the groups associated with your app. This means that you can manage their ownership, membership, and tags.\n\nTo create a group associated with your app, navigate to the app\'s page and click the "Create App Group" button. Once you have created the group, you can add members or roles to it from the group\'s page.',
    'Managing tags for your app':
      "As an app owner, you can manage the set of tags applied to your app. These tags and their associated constraints are inherited by all the groups associated with your app. To modify your app's tags, navigate to the app's page and click the edit button (pencil icon).",
    'Responding to group creation requests':
      "If your organization has the notification system set up, you will receive a notification when someone requests to create an app group associated with an app that you own. Otherwise, you can see group requests for apps you own under ‘Group Requests' > 'Assigned to Me'.\n\nFrom there, you can click 'View' to see the details of the group request, modify the group details associated with the request, provide a reason for your decision, and then either approve or reject the request.",
  },
  admin: {
    [`About ${appName} administrators`]: `${appName} administrators are the users who are members of the App-${appName} -Owners group. Administrators are able create or edit all groups, roles, and apps and manage the access for any group in the system. Administrators can also create tags, set tag constraints, apply tags to groups or apps, and remove tags.`,
    'Creating an app':
      "To create an app, click the 'Create App' button at the top of the 'Apps' page. That will open a dialog where you can set the app's name and description. The creator of the application will be automatically assigned as the initial owner of the application.\n\nIf the new App is meant to represent an application in Okta, you can optionally 'Assign' and/or add as a 'Push group' the associated app groups in the Okta Administrator dashboard for the Okta application.",
    'Creating a group':
      "On the 'Groups' page, click the 'Create Group' button at the top. This will open a dialog where you can select the group type, which app it is associated with (if it's an app group), and set the group name, description, and tags.\n\nNote that there is a separate flow for app owners to create groups associated with their app, starting from the app's page, for more details see the \"Guide for App Owners\".",
    'Creating tags, managing tag constraints, and removing tags':
      "To create a tag, navigate to the 'Tags' page by clicking the button labeled 'Tags' from the 'Groups' or 'Apps' pages. Then, click the 'Create Tag' button at the top of the page. This opens the tag creation dialog. From here, you can set the tag's name and description and, optionally, apply constraints to the tag.\n\nThe possible constraints at the time of writing are setting an ownership or membership time limit, requiring a reason for ownership or membership access changes, and disallowing group owners adding themselves as owners or members of the group. At the top of the dialog, there is a toggle that allows you to enable or diable the tag. If the tag is disabled, the tag constraints will not be enforced.\n\nAfter creating a tag, you can apply it to apps and groups from the tag's page. If you add the tag to an app, all of the app's app groups (including app groups created in the future) will inherit the tag and any constraints associated with it.\n\nFrom each tag's page, you can additionally remove the tag from apps and groups by clicking the X on that row and edit the tag's details by clicking the pencil icon to the right of the tag's name.\n\nYou can also add or remove tags from each app or group's individual pages from the dialog opened by clicking the pencil icon to the right of the app or group's name.",
    'Responding to group creation requests':
      "If your organization has the notification system set up, you will receive a notification when someone requests to create a role, group, or app group for an unowned app. Otherwise, you can see group requests for that you may respond to under ‘Group Requests' > 'Assigned to Me'.\n\nFrom there, you can click 'View' to see the details of the group request, modify the group details, provide a reason for your decision, and then either approve or reject the request.",
  },
  faq: {
    'I need to add a role as a member or owner of a group I do not own':
      "In the menu bar, there is a tab called 'Role Requests.' From there, if you own at least one role, you can click the 'Create Request' button to create a request for your role to be added to a group. If your organization has notifications enabled, a notification will be sent to the group owner(s) about the role request. If you do not own any roles, you will not be able to create a role request and you will need to reach out to the role owner.",
    'I need to create a new group/role': `If you are an ${appName} admin, please click [[admin--creating-a-group|here]] to see the 'Admins' user guide for step-by-step instructions.\n\nIf you are not an ${appName} admin, please see the instructions [[users--creating-a-group-creation-request|here]].`,
    'I need to create a new app': `If you are an ${appName} admin, please click [[admin--creating-an-app|here]] to see step-by-step instructions.\n\nIf you are not an ${appName} admin, please reach out to one for app creation. At this moment, there is not a way to request that an app is created through ${appName}.`,
    'I want to set my up my group to enforce a constraint (like a maximum membership duration)': `Tags can be used to enforce a variety of constraints, including enforcing a maximum membership duration. If a tag exists that has the constraint you are looking for enabled, you can apply it to any group you own from the dialog that is opened by clicking the pencil icon next to the group's name. If a tag does not exist, reach out to an ${appName} admin to create the tag for you.`,
    'I lost access to something! How can I see more information?':
      'Each user has an audit page that can be accessed from their user page by clicking the clock-arrow icon to the right of their name. On this page, you can see your complete ownership and membership history, when each ownership or membership started and ended, who added or removed your access, and the reason you were added to the group if one was provided. You can filter, sort, and search through this information to troubleshoot your lost access.',
    'I added a role to a group but users in the role were added to the group for less time than I set...':
      "This could happen for a couple of different reasons. Tags may limit the maximum membership or ownership time for groups. If you are adding the group from the role's page and selected the access time before choosing which group to add, the access time may be lowered to fit the group's constraint. \n\nAccess time when adding a role to a group also depends on for how long users are a member of the role. When adding a role to a group, access time is set as the minimum of each user's remaining role membership time and the time selected for the role's access to the group. ",
  },
};

// Convert string into a URL-safe hash fragment
function toSlug(section: string, question: string) {
  return `${section}--${question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '')}`;
}

// Turn [[slug|label]] into internal nav links and ((url|label)) into external hyperlinks
function AnswerContent({text, onNavigate}: {text: string; onNavigate: (slug: string) => void}) {
  const parts = text.split(/(\[\[[^\]]+\]\]|\(\([^)]+\)\))/g);
  return (
    <div style={{whiteSpace: 'pre-wrap'}}>
      {parts.map((part, i) => {
        const internal = part.match(/^\[\[([^|]+)\|([^\]]+)\]\]$/);
        if (internal) {
          const [, slug, label] = internal;
          return (
            <Box
              key={i}
              component="a"
              href={`#${slug}`}
              onClick={(e: React.MouseEvent) => {
                e.preventDefault();
                onNavigate(slug);
              }}
              sx={{color: 'primary.main', textDecoration: 'underline', cursor: 'pointer'}}>
              {label}
            </Box>
          );
        }
        const external = part.match(/^\(\(([^|]+)\|([^)]+)\)\)$/);
        if (external) {
          const [, url, label] = external;
          return (
            <Box
              key={i}
              component="a"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              sx={{color: 'primary.main', textDecoration: 'underline', cursor: 'pointer'}}>
              {label}
            </Box>
          );
        }
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </div>
  );
}

interface AccordionMakerProps {
  which: string;
  expandedSlug: string | null;
  onSlugChange: (slug: string | null) => void;
  onInternalLink: (slug: string) => void;
}

function AccordionMaker({which, expandedSlug, onSlugChange, onInternalLink}: AccordionMakerProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const handleChange = (slug: string) => (_: React.SyntheticEvent, isExpanded: boolean) => {
    const next = isExpanded ? slug : null;
    onSlugChange(next);
    navigate({hash: next ? `#${slug}` : ''}, {replace: true});
  };

  const handleCopyLink = (e: React.MouseEvent, slug: string) => {
    e.stopPropagation();
    const url = `${window.location.origin}${location.pathname}#${slug}`;
    navigator.clipboard.writeText(url);
  };

  return (
    <>
      <Typography variant="h5" color="text.accent" sx={{mt: 0, mb: '15px'}}>
        {sections[which][0]}
      </Typography>
      {Object.entries(guide[which]).map(([question, answer]) => {
        const slug = toSlug(which, question);
        const isExpanded = expandedSlug === slug;
        return (
          <Accordion key={slug} expanded={isExpanded} onChange={handleChange(slug)}>
            <AccordionSummary
              expandIcon={<ExpandMoreIcon />}
              aria-controls={`${slug}-content`}
              id={`${slug}-header`}
              sx={{fontWeight: 500}}>
              <Box sx={{display: 'flex', alignItems: 'center', width: '100%', pr: 1}}>
                <Box sx={{flexGrow: 1}}>{question}</Box>
                <Tooltip title="Copy link to this section">
                  <IconButton
                    size="small"
                    onClick={(e) => handleCopyLink(e, slug)}
                    aria-label="Copy link"
                    sx={{ml: 1, opacity: 0.5, '&:hover': {opacity: 1}}}>
                    <LinkIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </AccordionSummary>
            <AccordionDetails>
              <AnswerContent text={answer} onNavigate={onInternalLink} />
            </AccordionDetails>
          </Accordion>
        );
      })}
    </>
  );
}

export default function Home() {
  const currentUser = useCurrentUser();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();

  const hashSlug = location.hash.slice(1) || null;
  const sectionFromHash = hashSlug ? hashSlug.split('--')[0] : null;

  const [whichAccordion, setWhichAccordion] = React.useState<string>(
    sectionFromHash && sections[sectionFromHash] ? sectionFromHash : 'general',
  );
  const [expandedSlug, setExpandedSlug] = React.useState<string | null>(hashSlug);

  const [openDialog, setOpenDialog] = React.useState<'access' | 'role' | 'group' | null>(null);

  const [now, inOneWeek, inTwoDays] = React.useMemo(() => {
    const t = Math.floor(Date.now() / 1000);
    return [t, t + 7 * 24 * 60 * 60, t + 2 * 24 * 60 * 60];
  }, []);

  const {data: accessRequestsData} = useGetRequests({
    queryParams: {assignee_user_id: '@me', status: 'PENDING', page: 0, per_page: 1},
  });
  const {data: roleRequestsData} = useGetRoleRequests({
    queryParams: {assignee_user_id: '@me', status: 'PENDING', page: 0, per_page: 1},
  });
  const {data: groupRequestsData} = useGetGroupRequests({
    queryParams: {assignee_user_id: '@me', status: 'PENDING', page: 0, per_page: 1},
  });
  const {data: expiringGroupsData} = useGetUserGroupAudits({
    queryParams: {
      owner_id: '@me',
      active: true,
      start_date: now,
      end_date: inOneWeek,
      page: 0,
      per_page: 1,
      direct: true,
      deleted: false,
    },
  });
  const {data: expiringRolesData} = useGetGroupRoleAudits({
    queryParams: {owner_id: '@me', active: true, start_date: now, end_date: inOneWeek, page: 0, per_page: 1},
  });
  const {data: myAccessExpiringData} = useGetUserGroupAudits({
    queryParams: {
      user_id: '@me',
      active: true,
      start_date: now,
      end_date: inOneWeek,
      page: 0,
      per_page: 1,
      direct: true,
      deleted: false,
    },
  });
  const {data: myRolesExpiringData} = useGetGroupRoleAudits({
    queryParams: {role_owner_id: '@me', active: true, start_date: now, end_date: inOneWeek, page: 0, per_page: 1},
  });
  const {data: urgentExpiringGroupsData} = useGetUserGroupAudits({
    queryParams: {
      owner_id: '@me',
      active: true,
      start_date: now,
      end_date: inTwoDays,
      page: 0,
      per_page: 1,
      direct: true,
      deleted: false,
    },
  });
  const {data: urgentExpiringRolesData} = useGetGroupRoleAudits({
    queryParams: {owner_id: '@me', active: true, start_date: now, end_date: inTwoDays, page: 0, per_page: 1},
  });
  const {data: urgentMyAccessData} = useGetUserGroupAudits({
    queryParams: {
      user_id: '@me',
      active: true,
      start_date: now,
      end_date: inTwoDays,
      page: 0,
      per_page: 1,
      direct: true,
      deleted: false,
    },
  });
  const {data: urgentMyRolesData} = useGetGroupRoleAudits({
    queryParams: {role_owner_id: '@me', active: true, start_date: now, end_date: inTwoDays, page: 0, per_page: 1},
  });

  const statCounts: Record<string, number> = {
    'access-requests': accessRequestsData?.total ?? 0,
    'role-requests': roleRequestsData?.total ?? 0,
    'group-requests': groupRequestsData?.total ?? 0,
    'expiring-access': expiringGroupsData?.total ?? 0,
    'expiring-roles': expiringRolesData?.total ?? 0,
    'my-access-expiring': myAccessExpiringData?.total ?? 0,
    'my-roles-expiring': myRolesExpiringData?.total ?? 0,
  };

  const expiringColor = (urgentData: {total?: number} | undefined) =>
    (urgentData?.total ?? 0) > 0 ? '#EF4444' : '#F59E0B';

  const statColors: Record<string, string> = {
    'expiring-access': expiringColor(urgentExpiringGroupsData),
    'expiring-roles': expiringColor(urgentExpiringRolesData),
    'my-access-expiring': expiringColor(urgentMyAccessData),
    'my-roles-expiring': expiringColor(urgentMyRolesData),
  };

  const cardRef = React.useRef<HTMLDivElement>(null);
  const [cardWidth, setCardWidth] = React.useState(0);
  const lockedSectionWidth = React.useRef(0);

  React.useEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      if (lockedSectionWidth.current === 0 && w > 0) {
        // Lock the per-section width based on the initial 5-section layout
        lockedSectionWidth.current = w / INITIAL_SECTIONS;
      }
      setCardWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const sectionW = lockedSectionWidth.current;
  // Filter out items with zero counts (action items always show)
  const filteredStats = STAT_CONFIGS.filter((s) => s.isAction || (statCounts[s.id] ?? 0) > 0);
  // How many sections fit in one row
  const sectionsPerRow =
    sectionW > 0 && cardWidth > 0
      ? Math.min(Math.max(1, Math.floor(cardWidth / sectionW)), INITIAL_SECTIONS)
      : INITIAL_SECTIONS;
  // Use two rows only when fewer than 3 items fit per row
  const rows = sectionsPerRow < 3 ? 2 : 1;
  const visibleStats = filteredStats.slice(0, sectionsPerRow * rows);
  const row1Stats = visibleStats.slice(0, sectionsPerRow);
  const row2Stats = rows === 2 ? visibleStats.slice(sectionsPerRow) : [];

  useEffect(() => {
    if (!hashSlug) return;
    const section = hashSlug.split('--')[0];
    if (sections[section]) {
      setWhichAccordion(section);
      setExpandedSlug(hashSlug);
      setTimeout(() => {
        document.getElementById(`${hashSlug}-header`)?.scrollIntoView({behavior: 'smooth', block: 'start'});
      }, 100);
    }
  }, [hashSlug]);

  const handleSectionChange = (key: string) => {
    setWhichAccordion(key);
    setExpandedSlug(null);
    navigate({hash: ''}, {replace: true});
  };

  const handleInternalLink = (slug: string) => {
    const section = slug.split('--')[0];
    if (sections[section]) {
      setWhichAccordion(section);
      setExpandedSlug(slug);
      navigate({hash: `#${slug}`}, {replace: true});
      setTimeout(() => {
        document.getElementById(`${slug}-header`)?.scrollIntoView({behavior: 'smooth', block: 'start'});
      }, 100);
    }
  };

  return (
    <React.Fragment>
      <Paper ref={cardRef} sx={{mb: 3, display: 'flex', flexDirection: 'column', overflow: 'hidden'}}>
        {[row1Stats, row2Stats].map((rowStats, rowIndex) =>
          rowStats.length === 0 ? null : (
            <React.Fragment key={rowIndex}>
              {rowIndex > 0 && <Divider />}
              <Box sx={{display: 'flex', alignItems: 'stretch', justifyContent: 'center', minHeight: 98}}>
                {rowStats.map((stat, i) => {
                  const color = stat.isAction
                    ? theme.palette.text.accent
                    : statColors[stat.id] ?? stat.color ?? theme.palette.text.secondary;
                  return (
                    <React.Fragment key={stat.id}>
                      {i > 0 && <Divider orientation="vertical" flexItem sx={{my: 1.5}} />}
                      <Box
                        onClick={() => {
                          if (stat.id === 'make-access-request') setOpenDialog('access');
                          else if (stat.id === 'make-role-request') setOpenDialog('role');
                          else if (stat.id === 'make-group-request') setOpenDialog('group');
                          else if (stat.id === 'explore-user-docs') handleSectionChange('users');
                          else if (stat.id === 'explore-group-owner-docs') handleSectionChange('people-lead');
                          else if (stat.path) navigate(stat.path);
                        }}
                        sx={{
                          ...(sectionW > 0 ? {width: sectionW, flexShrink: 0} : {flex: 1}),
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          py: 2.5,
                          px: 3,
                          cursor: 'pointer',
                          transition: 'background-color 0.15s',
                          '&:hover': {bgcolor: alpha(color, 0.04)},
                        }}>
                        <Box
                          sx={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: stat.isAction ? 'center' : 'flex-start',
                            width: '100%',
                          }}>
                          {stat.isAction ? (
                            <Typography variant="body2" fontWeight={600} sx={{color}}>
                              {stat.label}
                            </Typography>
                          ) : (
                            <Box>
                              <Typography variant="h4" fontWeight={700} sx={{color, lineHeight: 1}}>
                                {statCounts[stat.id] ?? 0}
                              </Typography>
                              <Typography variant="caption" color="text.secondary" sx={{mt: 0.5, display: 'block'}}>
                                {statCounts[stat.id] === 1 ? stat.singularLabel ?? stat.label : stat.label}
                              </Typography>
                            </Box>
                          )}
                          <Box
                            sx={{
                              p: 1,
                              borderRadius: 2,
                              backgroundColor: alpha(color, 0.1),
                              color,
                              display: 'flex',
                              flexShrink: 0,
                            }}>
                            <stat.Icon />
                          </Box>
                        </Box>
                      </Box>
                    </React.Fragment>
                  );
                })}
              </Box>
            </React.Fragment>
          ),
        )}
      </Paper>
      <Paper>
        <Grid container spacing={2} direction="row" sx={{padding: 2}}>
          <Grid item xs={3}>
            <Grid container spacing={2} justifyContent="center" direction="column">
              <Grid item xs={12}>
                <Grid container justifyContent="center">
                  <Grid item>
                    <Typography variant="h5" fontWeight={500} color="text.accent">
                      {appName} User Guides
                    </Typography>
                  </Grid>
                </Grid>
              </Grid>
              {Object.entries(sections).map(([key, [, buttonTitle, icon]]) => (
                <Grid item xs={12} key={key}>
                  <Button
                    variant="contained"
                    size="large"
                    startIcon={icon}
                    sx={{width: '100%', height: '50px'}}
                    onClick={() => handleSectionChange(key)}>
                    {buttonTitle}
                  </Button>
                </Grid>
              ))}
            </Grid>
          </Grid>
          <Grid item xs={0.1}>
            <Divider orientation="vertical" />
          </Grid>
          <Grid item xs={8.8}>
            <AccordionMaker
              which={whichAccordion}
              expandedSlug={expandedSlug}
              onSlugChange={setExpandedSlug}
              onInternalLink={handleInternalLink}
            />
          </Grid>
        </Grid>
      </Paper>
      <CreateAccessRequest
        currentUser={currentUser}
        open={openDialog === 'access'}
        setOpen={(o) => setOpenDialog(o ? 'access' : null)}
      />
      <CreateRoleRequest
        currentUser={currentUser}
        enabled={true}
        open={openDialog === 'role'}
        setOpen={(o) => setOpenDialog(o ? 'role' : null)}
      />
      <CreateGroupRequest
        currentUser={currentUser}
        open={openDialog === 'group'}
        setOpen={(o) => setOpenDialog(o ? 'group' : null)}
      />
    </React.Fragment>
  );
}
