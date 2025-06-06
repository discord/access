import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  List,
  ListItem,
  ListItemText,
  Chip,
  CircularProgress,
  Alert,
  Divider,
  Autocomplete,
  TextField,
  Button,
  Grid,
} from '@mui/material';
import {useGetAllUsers, useGetUserById} from '../../api/apiComponents';
import {OktaUser, OktaUserGroupMember} from '../../api/apiSchemas';

const groupTypeLabels = {
  role_group: 'Role Group',
  app_group: 'App Group',
  okta_group: 'Okta Group',
};

const findCommonMemberships = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (user1.id === user2.id) {
    return user1?.active_group_memberships || [];
  }

  if (!user1?.active_group_memberships?.length || !user2?.active_group_memberships?.length) {
    console.log('DEBUG - One user has no memberships, returning empty array');
    return [];
  }

  const commonMemberships: OktaUserGroupMember[] = [];

  for (const membership1 of user1.active_group_memberships) {
    const groupId1 = membership1.group?.id || membership1.active_group?.id;

    if (!groupId1) {
      continue;
    }
    // Check if user2 has a membership with the same group ID
    const hasCommonGroup = user2.active_group_memberships.some((membership2) => {
      const groupId2 = membership2.group?.id || membership2.active_group?.id;
      return groupId1 === groupId2;
    });

    if (hasCommonGroup) {
      commonMemberships.push(membership1);
    }
  }
  return commonMemberships;
};

// Function to find groups where both users have ownerships
const findCommonOwnerships = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (user1.id === user2.id) {
    return user1?.active_group_ownerships || [];
  }

  if (!user1?.active_group_ownerships?.length || !user2?.active_group_ownerships?.length) {
    return [];
  }

  const commonOwnerships: OktaUserGroupMember[] = [];

  for (const ownership1 of user1.active_group_ownerships) {
    const groupId1 = ownership1.group?.id || ownership1.active_group?.id;

    if (!groupId1) {
      continue;
    }

    const hasCommonGroup = user2.active_group_ownerships.some((ownership2) => {
      const groupId2 = ownership2.group?.id || ownership2.active_group?.id;
      return groupId1 === groupId2;
    });

    if (hasCommonGroup) {
      commonOwnerships.push(ownership1);
    }
  }

  return commonOwnerships;
};

// Function to find memberships unique to user1
const findUniqueMembershipsUser1 = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (!user1?.active_group_memberships?.length) {
    return [];
  }

  // If user2 has no memberships, all of user1's memberships are unique
  if (!user2?.active_group_memberships?.length) {
    return user1.active_group_memberships;
  }

  // Collect all group IDs from user2 for comparison
  const user2GroupIds = new Set<string>();

  user2.active_group_memberships.forEach((membership) => {
    const groupId = membership.group?.id || membership.active_group?.id;
    if (groupId) {
      user2GroupIds.add(groupId);
    }
  });

  // Return user1's memberships that are not in user2's groups
  return user1.active_group_memberships.filter((membership) => {
    const groupId = membership.group?.id || membership.active_group?.id;
    return groupId && !user2GroupIds.has(groupId);
  });
};

// Function to find memberships unique to user2
const findUniqueMembershipsUser2 = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (!user2?.active_group_memberships?.length) {
    return [];
  }

  // If user1 has no memberships, all of user2's memberships are unique
  if (!user1?.active_group_memberships?.length) {
    return user2.active_group_memberships;
  }

  // Collect all group IDs from user1 for comparison
  const user1GroupIds = new Set<string>();

  user1.active_group_memberships.forEach((membership) => {
    const groupId = membership.group?.id || membership.active_group?.id;
    if (groupId) {
      user1GroupIds.add(groupId);
    }
  });

  // Return user2's memberships that are not in user1's groups
  return user2.active_group_memberships.filter((membership) => {
    const groupId = membership.group?.id || membership.active_group?.id;
    return groupId && !user1GroupIds.has(groupId);
  });
};

// Function to find ownerships unique to user1
const findUniqueOwnershipsUser1 = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (!user1?.active_group_ownerships?.length) {
    return [];
  }

  // If user2 has no ownerships, all of user1's ownerships are unique
  if (!user2?.active_group_ownerships?.length) {
    return user1.active_group_ownerships;
  }

  // Collect all group IDs from user2 for comparison
  const user2GroupIds = new Set<string>();

  user2.active_group_ownerships.forEach((ownership) => {
    const groupId = ownership.group?.id || ownership.active_group?.id;
    if (groupId) {
      user2GroupIds.add(groupId);
    }
  });

  // Return user1's ownerships that are not in user2's groups
  return user1.active_group_ownerships.filter((ownership) => {
    const groupId = ownership.group?.id || ownership.active_group?.id;
    return groupId && !user2GroupIds.has(groupId);
  });
};

// Function to find ownerships unique to user2
const findUniqueOwnershipsUser2 = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  if (!user2?.active_group_ownerships?.length) {
    return [];
  }

  // If user1 has no ownerships, all of user2's ownerships are unique
  if (!user1?.active_group_ownerships?.length) {
    return user2.active_group_ownerships;
  }

  // Collect all group IDs from user1 for comparison
  const user1GroupIds = new Set<string>();

  user1.active_group_ownerships.forEach((ownership) => {
    const groupId = ownership.group?.id || ownership.active_group?.id;
    if (groupId) {
      user1GroupIds.add(groupId);
    }
  });

  // Return user2's ownerships that are not in user1's groups
  return user2.active_group_ownerships.filter((ownership) => {
    const groupId = ownership.group?.id || ownership.active_group?.id;
    return groupId && !user1GroupIds.has(groupId);
  });
};

interface MembershipListProps {
  memberships: OktaUserGroupMember[];
  emptyMessage: string;
}

function MembershipList({memberships, emptyMessage}: MembershipListProps) {
  if (memberships.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{p: 2}}>
        {emptyMessage}
      </Typography>
    );
  }

  return (
    <List>
      {memberships.map((membership, index) => {
        const groupId = membership.group?.id || membership.active_group?.id;
        const groupName = membership.group?.name || membership.active_group?.name;
        const groupType = membership.group?.type || membership.active_group?.type;

        return (
          <React.Fragment key={groupId || index}>
            <ListItem>
              <ListItemText
                primary={
                  <Box display="flex" alignItems="center" gap={1}>
                    <Typography variant="body1">{groupName || 'Unnamed Group'}</Typography>
                    <Chip
                      label={
                        groupType ? groupTypeLabels[groupType as keyof typeof groupTypeLabels] || 'unknown' : 'unknown'
                      }
                      size="small"
                      color={groupType === 'group' ? 'primary' : 'secondary'}
                      variant="outlined"
                    />
                  </Box>
                }
                secondary={membership.group?.description || membership.active_group?.description || ''}
              />
            </ListItem>
            {index < memberships.length - 1 && <Divider />}
          </React.Fragment>
        );
      })}
    </List>
  );
}

export default function DiffUsers() {
  // Store the basic user selections from the dropdown
  const [selectedUser1, setSelectedUser1] = React.useState<OktaUser | null>(null);
  const [selectedUser2, setSelectedUser2] = React.useState<OktaUser | null>(null);
  const [isSameUser, setIsSameUser] = React.useState<boolean>(false);
  const [tabValue, setTabValue] = React.useState(0);

  // Fetch all users for the dropdown options
  const {data: allUsers, isLoading: isLoadingAllUsers, error: errorAllUsers} = useGetAllUsers({});

  // Fetch detailed user data after selection
  const {
    data: user1Details,
    isLoading: isLoadingUser1,
    error: errorUser1,
  } = useGetUserById(
    {
      pathParams: {userId: selectedUser1?.id || ''},
    },
    {
      enabled: !!selectedUser1?.id && !!selectedUser2?.id, // Only fetch when both users are selected
    },
  );

  const {
    data: user2Details,
    isLoading: isLoadingUser2,
    error: errorUser2,
  } = useGetUserById(
    {
      pathParams: {userId: selectedUser2?.id || ''},
    },
    {
      enabled: !!selectedUser1?.id && !!selectedUser2?.id, // Only fetch when both users are selected
    },
  );

  const isLoading = isLoadingAllUsers || (selectedUser1 && isLoadingUser1) || (selectedUser2 && isLoadingUser2);
  const error = errorAllUsers || errorUser1 || errorUser2;

  // Reset the comparison view
  const resetComparison = () => {
    setSelectedUser1(null);
    setSelectedUser2(null);
    setIsSameUser(false);
  };

  // Handle tab change
  const handleTabChange = (event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
  };

  // Update isSameUser flag whenever selections change
  React.useEffect(() => {
    if (selectedUser1 && selectedUser2) {
      setIsSameUser(selectedUser1.id === selectedUser2.id);
    } else {
      setIsSameUser(false);
    }
  }, [selectedUser1, selectedUser2]);

  if (error) {
    return (
      <Alert severity="error">Error loading users: {error instanceof Error ? error.message : 'Unknown error'}</Alert>
    );
  }

  if (isLoadingAllUsers) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
        <CircularProgress />
      </Box>
    );
  }

  // Show user selection screen if no users selected or if detailed data is not loaded
  if (!selectedUser1 || !selectedUser2 || !user1Details || !user2Details) {
    return (
      <Box>
        <Typography variant="h4" component="h1" gutterBottom>
          Compare Users
        </Typography>
        <Typography variant="body1" color="text.secondary" gutterBottom>
          Select two users to compare their memberships and access.
        </Typography>

        <Box display="flex" gap={2} mt={3}>
          <Card sx={{flex: 1}}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                First User
              </Typography>
              <Autocomplete
                options={allUsers?.results ?? []}
                getOptionLabel={(option: OktaUser) => `${option.first_name} ${option.last_name} (${option.email})`}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Select first user"
                    placeholder="Search users..."
                    variant="outlined"
                    fullWidth
                  />
                )}
                onChange={(event, value) => {
                  if (value) {
                    setSelectedUser1(value);
                  }
                }}
                renderOption={(props, option) => (
                  <Box component="li" {...props}>
                    <Box>
                      <Typography variant="body1">
                        {option.first_name} {option.last_name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {option.email}
                      </Typography>
                    </Box>
                  </Box>
                )}
              />
            </CardContent>
          </Card>

          <Card sx={{flex: 1}}>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Second User
              </Typography>
              <Autocomplete
                options={allUsers?.results ?? []}
                getOptionLabel={(option: OktaUser) => `${option.first_name} ${option.last_name} (${option.email})`}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Select second user"
                    placeholder="Search users..."
                    variant="outlined"
                    fullWidth
                  />
                )}
                onChange={(event, value) => {
                  if (value) {
                    setSelectedUser2(value);
                  }
                }}
                renderOption={(props, option) => (
                  <Box component="li" {...props}>
                    <Box>
                      <Typography variant="body1">
                        {option.first_name} {option.last_name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {option.email}
                      </Typography>
                    </Box>
                  </Box>
                )}
              />
            </CardContent>
          </Card>
        </Box>
      </Box>
    );
  }

  if (isLoadingUser1 || isLoadingUser2) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
        <CircularProgress />
      </Box>
    );
  }

  // Calculate common and unique memberships/ownerships
  const commonMemberships = findCommonMemberships(user1Details, user2Details);
  const commonOwnerships = findCommonOwnerships(user1Details, user2Details);

  const uniqueMembershipsUser1 = findUniqueMembershipsUser1(user1Details, user2Details);
  const uniqueMembershipsUser2 = findUniqueMembershipsUser2(user1Details, user2Details);

  const uniqueOwnershipsUser1 = findUniqueOwnershipsUser1(user1Details, user2Details);
  const uniqueOwnershipsUser2 = findUniqueOwnershipsUser2(user1Details, user2Details);

  // Log user properties for debugging
  console.log('User comparison data:', {
    user1: user1Details,
    user2: user2Details,
    commonMembershipsCount: commonMemberships.length,
    commonOwnershipsCount: commonOwnerships.length,
  });

  return (
    <Box>
      <Typography variant="h4" component="h1" gutterBottom>
        User Access Comparison
      </Typography>

      <Box display="flex" justifyContent="space-between" alignItems="center" mb={3}>
        <Button variant="outlined" onClick={resetComparison}>
          Compare Different Users
        </Button>
        {isSameUser && (
          <Alert severity="info" sx={{flex: 1, ml: 2}}>
            Same user selected in both dropdowns. Showing all access.
          </Alert>
        )}
      </Box>

      <Box display="flex" gap={2} mb={3}>
        <Card sx={{flex: 1}}>
          <CardContent>
            <Typography variant="h6" component="h2">
              {user1Details.first_name} {user1Details.last_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {user1Details.email}
            </Typography>
            <Box mt={1}>
              <Typography variant="body2">
                Group memberships: {user1Details.active_group_memberships?.length || 0}
              </Typography>
              <Typography variant="body2">
                Group ownerships: {user1Details.active_group_ownerships?.length || 0}
              </Typography>
            </Box>
          </CardContent>
        </Card>

        <Card sx={{flex: 1}}>
          <CardContent>
            <Typography variant="h6" component="h2">
              {user2Details.first_name} {user2Details.last_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {user2Details.email}
            </Typography>
            <Box mt={1}>
              <Typography variant="body2">
                Group memberships: {user2Details.active_group_memberships?.length || 0}
              </Typography>
              <Typography variant="body2">
                Group ownerships: {user2Details.active_group_ownerships?.length || 0}
              </Typography>
            </Box>
          </CardContent>
        </Card>
      </Box>

      {/* Common Groups Section */}
      <Typography variant="h5" gutterBottom mt={4}>
        Common Access
      </Typography>
      <Grid container spacing={3}>
        {/* Common Memberships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {isSameUser ? 'All Memberships' : 'Common Memberships'} ({commonMemberships.length})
              </Typography>
              <MembershipList
                memberships={commonMemberships}
                emptyMessage="No common memberships found between these users."
              />
            </CardContent>
          </Card>
        </Grid>

        {/* Common Ownerships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {isSameUser ? 'All Ownerships' : 'Common Ownerships'} ({commonOwnerships.length})
              </Typography>
              <MembershipList
                memberships={commonOwnerships}
                emptyMessage="No common ownerships found between these users."
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Unique Memberships Section */}
      <Typography variant="h5" gutterBottom mt={4}>
        Unique Memberships
      </Typography>
      <Grid container spacing={3}>
        {/* User 1's Unique Memberships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {user1Details.first_name}'s Unique Memberships ({uniqueMembershipsUser1.length})
              </Typography>
              <MembershipList
                memberships={uniqueMembershipsUser1}
                emptyMessage={`${user1Details.first_name} has no unique memberships compared to ${user2Details.first_name}.`}
              />
            </CardContent>
          </Card>
        </Grid>

        {/* User 2's Unique Memberships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {user2Details.first_name}'s Unique Memberships ({uniqueMembershipsUser2.length})
              </Typography>
              <MembershipList
                memberships={uniqueMembershipsUser2}
                emptyMessage={`${user2Details.first_name} has no unique memberships compared to ${user1Details.first_name}.`}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Unique Ownerships Section */}
      <Typography variant="h5" gutterBottom mt={4}>
        Unique Ownerships
      </Typography>
      <Grid container spacing={3}>
        {/* User 1's Unique Ownerships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {user1Details.first_name}'s Unique Ownerships ({uniqueOwnershipsUser1.length})
              </Typography>
              <MembershipList
                memberships={uniqueOwnershipsUser1}
                emptyMessage={`${user1Details.first_name} has no unique ownerships compared to ${user2Details.first_name}.`}
              />
            </CardContent>
          </Card>
        </Grid>

        {/* User 2's Unique Ownerships */}
        <Grid item xs={12} md={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {user2Details.first_name}'s Unique Ownerships ({uniqueOwnershipsUser2.length})
              </Typography>
              <MembershipList
                memberships={uniqueOwnershipsUser2}
                emptyMessage={`${user2Details.first_name} has no unique ownerships compared to ${user1Details.first_name}.`}
              />
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}
