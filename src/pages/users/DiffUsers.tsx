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
} from '@mui/material';
import {useQuery} from '@tanstack/react-query';
import {useGetAllUsers} from '../../api/apiComponents';
import {OktaUser, OktaUserGroupMember} from '../../api/apiSchemas';

interface User {
  id: string;
  name: string;
  email: string;
  memberships: Membership[];
}

interface Membership {
  id: string;
  name: string;
  type: 'group' | 'role';
  description?: string;
}

const fetchUser = async (userId: string): Promise<User> => {
  const response = await fetch(`/api/users/${userId}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch user ${userId}`);
  }
  return response.json();
};

const findCommonMemberships = (user1: OktaUser, user2: OktaUser): OktaUserGroupMember[] => {
  const user1MembershipIds = new Set(user1?.active_group_memberships?.map((m) => m.id) ?? []);
  return user2?.active_group_memberships?.filter((membership) => user1MembershipIds.has(membership.id)) ?? [];
};

export default function DiffUsers() {
  const [userId1, setUserId1] = React.useState<string | null>(null);
  const [userId2, setUserId2] = React.useState<string | null>(null);
  const [selectedUser1, setSelectedUser1] = React.useState<OktaUser | null>(null);
  const [selectedUser2, setSelectedUser2] = React.useState<OktaUser | null>(null);

  if (!userId1 || !userId2) {
    const {data, isLoading, error} = useGetAllUsers({});
    if (isLoading) {
      return (
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
          <CircularProgress />
        </Box>
      );
    }
    if (error) {
      return (
        <Alert severity="error">Error loading users: {error instanceof Error ? error.message : 'Unknown error'}</Alert>
      );
    }

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
                options={data?.results ?? []}
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
                    // Navigate to comparison with selected user
                    // You'll need to implement navigation logic here
                    console.log('Selected user:', value);
                    setUserId1(value.id);
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
                options={data?.results ?? []}
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
                    // Navigate to comparison with selected user
                    // You'll need to implement navigation logic here
                    console.log('Selected user:', value);
                    setUserId2(value.id);
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
  const {
    data: fetchUserId1,
    isLoading: isLoadingUser1,
    error: errorUser1,
  } = useQuery({
    queryKey: ['user', userId1],
    queryFn: () => fetchUser(userId1!),
    enabled: !!userId1,
  });

  const {
    data: fetchUserId2,
    isLoading: isLoadingUser2,
    error: errorUser2,
  } = useQuery({
    queryKey: ['user', userId2],
    queryFn: () => fetchUser(userId2!),
    enabled: !!userId2,
  });

  const isLoading = isLoadingUser1 || isLoadingUser2;
  const error = errorUser1 || errorUser2;

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="200px">
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error">Error loading users: {error instanceof Error ? error.message : 'Unknown error'}</Alert>
    );
  }

  const commonMemberships = findCommonMemberships(fetchUserId1, fetchUserId2);

  return (
    <Box>
      <Typography variant="h4" component="h1" gutterBottom>
        User Membership Comparison
      </Typography>

      <Box display="flex" gap={2} mb={3}>
        <Card sx={{flex: 1}}>
          <CardContent>
            <Typography variant="h6" component="h2">
              {selectedUser1?.first_name} {selectedUser1?.last_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {selectedUser1?.email}
            </Typography>
            <Typography variant="body2" sx={{mt: 1}}>
              Total memberships: {selectedUser1?.active_group_memberships?.length}
            </Typography>
          </CardContent>
        </Card>

        <Card sx={{flex: 1}}>
          <CardContent>
            <Typography variant="h6" component="h2">
              {selectedUser2?.first_name} {selectedUser2?.last_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {selectedUser2?.email}
            </Typography>
            <Typography variant="body2" sx={{mt: 1}}>
              Total memberships: {selectedUser2?.active_group_memberships?.length}
            </Typography>
          </CardContent>
        </Card>
      </Box>

      <Card>
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>
            Common Memberships ({commonMemberships.length})
          </Typography>

          {commonMemberships.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No common memberships found between these users.
            </Typography>
          ) : (
            <List>
              {commonMemberships.map((membership, index) => (
                <React.Fragment key={membership.id}>
                  <ListItem>
                    <ListItemText
                      primary={
                        <Box display="flex" alignItems="center" gap={1}>
                          <Typography variant="body1">{membership.group.name}</Typography>
                          <Chip
                            label={membership.group.type}
                            size="small"
                            color={membership.group.type === 'group' ? 'primary' : 'secondary'}
                            variant="outlined"
                          />
                        </Box>
                      }
                      secondary={membership.group.description}
                    />
                  </ListItem>
                  {index < commonMemberships.length - 1 && <Divider />}
                </React.Fragment>
              ))}
            </List>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
