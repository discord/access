import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  CircularProgress,
  Divider,
  Grid,
  Link,
  Pagination,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableFooter,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import {AppGroupForAppDetail, OktaUserGroupMemberDetail} from '../../../api/apiSchemas';
import {useGroupMemberDetailsById} from '../../../api/apiComponents';
import React from 'react';
import {displayUserName, groupMemberships, sortGroupMembers} from '../../../helpers';
import {EmptyListEntry} from '../../../components/EmptyListEntry';
import Ending from '../../../components/Ending';
import MarkdownDescription from '../../../components/MarkdownDescription';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {Link as RouterLink} from 'react-router-dom';

// Owners are few and rendered inline (un-paginated); fetch up to this many.
const OWNER_FETCH_SIZE = 100;
// Members page this many distinct users at a time via the page-number control.
const MEMBER_PAGE_SIZE = 100;

interface GroupDetailListProps {
  member_list: any[];
  title?: string;
  loading?: boolean;
}

const GroupDetailList: React.FC<GroupDetailListProps> = React.memo(
  ({member_list, title, loading}) => {
    return (
      <Stack direction="column" spacing={1}>
        {title && (
          <Typography variant="body1" component={'div'}>
            {title}
          </Typography>
        )}

        <TableContainer component={Paper}>
          <Table sx={{minWidth: 325}} size="small" aria-label="app group members">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Ending</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={3} align="center">
                    <CircularProgress size={20} />
                  </TableCell>
                </TableRow>
              ) : member_list.length > 0 ? (
                member_list.map((member: OktaUserGroupMemberDetail) => (
                  <TableRow key={member.active_user?.id}>
                    <TableCell>
                      <Link
                        to={`/users/${member.active_user?.email.toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {displayUserName(member.active_user)}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Link
                        to={`/users/${member.active_user?.email.toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {member.active_user?.email.toLowerCase()}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Ending memberships={[member]} />
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <EmptyListEntry cellProps={{colSpan: 3}} />
              )}
            </TableBody>

            <TableFooter>
              <TableRow />
            </TableFooter>
          </Table>
        </TableContainer>
      </Stack>
    );
  },
  (prevProps, nextProps) => {
    return (
      prevProps.title === nextProps.title &&
      prevProps.loading === nextProps.loading &&
      prevProps.member_list.length === nextProps.member_list.length &&
      prevProps.member_list.every(
        (member, index) => member.active_user?.id === nextProps.member_list[index]?.active_user?.id,
      )
    );
  },
);

const dedupeByUser = (rows: OktaUserGroupMemberDetail[] | undefined) =>
  Object.entries(groupMemberships(rows ?? []))
    .sort(sortGroupMembers)
    .map((entry) => entry[1][0]);

const AccordionItem: React.FC<{
  appGroup: AppGroupForAppDetail;
  expanded: boolean;
  onToggle: (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => void;
}> = React.memo(
  ({appGroup, expanded, onToggle}) => {
    const [memberPage, setMemberPage] = React.useState(1);

    // Members/owners are no longer inlined on the app payload; fetch them from
    // the member-details endpoint only when this group is expanded. Owners are
    // few (fetched whole); members page MEMBER_PAGE_SIZE at a time via the
    // page-number control below.
    const ownersQuery = useGroupMemberDetailsById(
      {pathParams: {groupId: appGroup.id}, queryParams: {owner: true, size: OWNER_FETCH_SIZE}},
      {enabled: expanded},
    );
    const membersQuery = useGroupMemberDetailsById(
      {pathParams: {groupId: appGroup.id}, queryParams: {owner: false, page: memberPage, size: MEMBER_PAGE_SIZE}},
      {enabled: expanded},
    );

    const owners = React.useMemo(() => dedupeByUser(ownersQuery.data?.items), [ownersQuery.data]);
    const members = React.useMemo(() => dedupeByUser(membersQuery.data?.items), [membersQuery.data]);
    const memberPages = membersQuery.data?.pages ?? 0;

    const handleToggle = React.useMemo(() => onToggle(appGroup.name), [onToggle, appGroup.name]);

    return (
      <TableContainer key={appGroup.id} component={Paper}>
        {/* unmountOnExit so collapsed groups don't fetch/render members — only
            the expanded group hits the member-details endpoint. */}
        <Accordion expanded={expanded} onChange={handleToggle} TransitionProps={{unmountOnExit: true}}>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Box sx={{display: 'inline-flex', flexGrow: 1}}>
              <Stack direction="column" spacing={1} sx={{flexGrow: 0.95}}>
                <Typography variant="h6" color="text.accent">
                  <Link
                    to={`/groups/${appGroup.name}`}
                    sx={{textDecoration: 'none', color: 'inherit'}}
                    component={RouterLink}>
                    {appGroup.name}
                  </Link>
                </Typography>
                <MarkdownDescription
                  description={appGroup.description}
                  sx={{mx: 0, width: 'auto', px: 0, color: 'grey'}}
                />
              </Stack>
              <Box sx={{display: 'flex', justifyContent: 'flex-end', alignItems: 'right'}}>
                <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                Owners: {appGroup.owner_count ?? 0} <br />
                Members: {appGroup.member_count ?? 0}
              </Box>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <Table aria-label="app group members">
              <TableBody className="accordion-body">
                <TableRow>
                  <TableCell colSpan={2}>
                    <Stack direction="row" useFlexGap flexWrap={'wrap'} justifyContent={'space-between'} gap={'2rem'}>
                      <GroupDetailList member_list={owners} title={'Group Owners'} loading={ownersQuery.isLoading} />
                      <Stack direction="column" spacing={1}>
                        <GroupDetailList member_list={members} title={'Members'} loading={membersQuery.isLoading} />
                        {memberPages > 1 && (
                          <Box sx={{display: 'flex', justifyContent: 'center'}}>
                            <Pagination
                              size="small"
                              count={memberPages}
                              page={memberPage}
                              onChange={(_, value) => setMemberPage(value)}
                              color="primary"
                            />
                          </Box>
                        )}
                      </Stack>
                    </Stack>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </AccordionDetails>
        </Accordion>
      </TableContainer>
    );
  },
  (prevProps, nextProps) => {
    return (
      prevProps.expanded === nextProps.expanded &&
      prevProps.appGroup.id === nextProps.appGroup.id &&
      prevProps.appGroup.name === nextProps.appGroup.name &&
      prevProps.appGroup.description === nextProps.appGroup.description &&
      prevProps.appGroup.member_count === nextProps.appGroup.member_count &&
      prevProps.appGroup.owner_count === nextProps.appGroup.owner_count
    );
  },
);

interface AppAccordionListGroupProps {
  app_group: AppGroupForAppDetail[];
  list_group_title?: string;
  list_group_description?: string;
  isExpanded?: boolean;
}

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = React.memo(
  ({app_group, list_group_title, list_group_description, isExpanded = false}) => {
    const [expanded, setExpanded] = React.useState<Record<string, boolean>>(() => {
      if (isExpanded && app_group) {
        const initialExpanded: Record<string, boolean> = {};
        app_group.forEach((group) => {
          initialExpanded[group.name] = true;
        });
        return initialExpanded;
      }
      return {};
    });

    const appGroupRef = React.useRef(app_group);
    appGroupRef.current = app_group;

    // Apply expand/collapse-all ONLY when the toggle flips — not when the group
    // list changes. Infinite scroll grows the list as you scroll (and a member
    // page-change can trigger a fetch), and re-running this on every list change
    // used to reset (collapse) accordions the user had opened. New groups follow
    // the current `isExpanded` via the render default below.
    React.useEffect(() => {
      const currentAppGroup = appGroupRef.current;
      if (currentAppGroup) {
        setExpanded((prevExpanded) => {
          const newExpanded = {...prevExpanded};
          let hasChanges = false;

          currentAppGroup.forEach((group) => {
            if (newExpanded[group.name] !== isExpanded) {
              newExpanded[group.name] = isExpanded;
              hasChanges = true;
            }
          });

          return hasChanges ? newExpanded : prevExpanded;
        });
      }
    }, [isExpanded]);

    const handleChange = React.useCallback(
      (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
        setExpanded((prev) => {
          if (prev[id] === newExpanded) {
            return prev;
          }
          return {...prev, [id]: newExpanded};
        });
      },
      [],
    );

    const appGroupList = React.useMemo(() => {
      return app_group || [];
    }, [app_group]);

    return (
      <React.Fragment>
        <Grid
          item
          xs={12}
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: '1.25rem',
          }}>
          {(appGroupList?.length || 0) > 0 && list_group_title && (
            <Typography variant="h5" component={'div'} fontWeight={500}>
              {list_group_title}
            </Typography>
          )}
          {(appGroupList?.length || 0) > 0 && list_group_description && (
            <MarkdownDescription description={list_group_description} sx={{mx: 0, width: 'auto', px: 0}} />
          )}
          {appGroupList.map((appGroup) => (
            <AccordionItem
              key={appGroup.id}
              appGroup={appGroup}
              expanded={expanded[appGroup.name] ?? isExpanded}
              onToggle={handleChange}
            />
          ))}
        </Grid>
      </React.Fragment>
    );
  },
  (prevProps, nextProps) => {
    return (
      prevProps.isExpanded === nextProps.isExpanded &&
      prevProps.list_group_title === nextProps.list_group_title &&
      prevProps.list_group_description === nextProps.list_group_description &&
      prevProps.app_group.length === nextProps.app_group.length &&
      prevProps.app_group.every(
        (group, index) =>
          group.id === nextProps.app_group[index]?.id &&
          group.name === nextProps.app_group[index]?.name &&
          group.description === nextProps.app_group[index]?.description &&
          group.member_count === nextProps.app_group[index]?.member_count &&
          group.owner_count === nextProps.app_group[index]?.owner_count,
      )
    );
  },
);
