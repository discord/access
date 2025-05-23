import React, {useEffect} from 'react';

interface ChangeTitleProps {
  title: string;
}

const ChangeTitle: React.FC<ChangeTitleProps> = ({title}) => {
  useEffect(() => {
    document.title = title;

    return () => {
      document.title = 'Access';
    };
  }, [title]);

  return null;
};

export default ChangeTitle;
